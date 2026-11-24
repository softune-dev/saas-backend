"""Shared plan-upgrade logic used by both a human (Superadmin's manual plan
change / confirm-renewal actions in app/api/superadmin.py) and the automated
bKash/bank SMS webhook (app/api/public.py's bkash_sms_webhook). Keeping this
in one place means an auto-verified payment produces exactly the same
tenant/invoice state a person clicking the button in Superadmin would have
produced — not a second, slightly-different code path.
"""

import calendar
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, invoices as invoices_module, plans, queue
from app.db import SessionLocal
from app.models import Invoice, Site, Tenant

log = logging.getLogger(__name__)


async def refresh_storefronts(tenant_id) -> None:
    """Call after ANY write to tenant.plan. The public site config and the
    assistant index are cached per host and now depend on the plan (Free hides
    tracking ids and gateways), so a cached copy from the old plan must not be
    served until the TTL runs out. Every site of the tenant, since the plan is
    per tenant. Best-effort: a cache or queue hiccup must never fail a plan
    change that is already saved. Its own session, because a failed read here
    would need a rollback, and that expires every row the caller still has to
    read for its confirmation email."""
    try:
        async with SessionLocal() as own:
            sites, _ = await crud.list_scoped(own, Site, tenant_id)
        for site in sites:
            await cache.invalidate_site(site.subdomain, site.custom_domain)
            await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})
    except Exception:
        log.exception("plan change: could not refresh the stores of tenant %s", tenant_id)


def add_one_month(dt: datetime) -> datetime:
    """Same date math as app/api/superadmin.py's own _add_one_month
    (intentionally duplicated, not imported, so neither module depends on
    the other's internals) — advances by exactly one calendar month, keeping
    the same day-of-month where possible (Jan 31 + 1 month -> Feb 28/29, not
    Mar 3). See migrations/065's docstring on why a naive +30 days drifts."""
    year = dt.year + dt.month // 12
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_renewal(old_plan: str, renews_at: datetime | None, now: datetime) -> datetime:
    """The next renewal date, given the plan the tenant had BEFORE this payment.

    A paying tenant always advances one month from its own date, never from
    "today", so paying a few days late doesn't move the due day (migrations/065).
    A tenant that was not on a paid plan (free after a lapse, trial, demo) may
    still carry a date from an earlier paid period: advancing that would charge
    them for months they did not have, and leave them overdue, then locked out,
    right after paying. They start from now. A date still in the future is time
    already paid for and is kept.
    """
    if renews_at is None or (old_plan not in plans.PAID_PLANS and renews_at < now):
        return add_one_month(now)
    return add_one_month(renews_at)


async def apply_paid_plan(db: AsyncSession, tenant: Tenant, plan: str) -> Invoice:
    """Puts a tenant onto a paid plan and issues the invoice for it — covers
    both a first-time purchase/upgrade (different plan, or coming off
    trial/demo) and a same-plan renewal, same as
    app/api/superadmin.py's update_tenant + confirm_plan_renewal do by hand.

    The renewal date is next_renewal's decision: one month on from where it
    already was for a paying tenant, or from now for one that wasn't.
    """
    now = datetime.now(UTC)
    tenant.plan_renews_at = next_renewal(tenant.plan, tenant.plan_renews_at, now)
    tenant.plan = plan
    tenant.plan_renewal_reminded_at = None
    tenant.plan_overdue_notified_at = None
    tenant.plan_overdue_since = None
    tenant.plan_deletion_warned_at = None
    if tenant.status == "payment_overdue":
        tenant.status = "active"
    tenant = await crud.save(db, tenant)

    invoice = Invoice(
        tenant_id=tenant.id,
        invoice_number=await crud.next_invoice_number(db, tenant.id),
        plan=tenant.plan,
        amount_cents=invoices_module.PLAN_PRICES_CENTS.get(tenant.plan, 0),
        currency="BDT",
        period_label=now.strftime("%b %Y"),
        tenant_business_snapshot=tenant.business,
    )
    await crud.save(db, invoice)
    await queue.publish(queue.JOB_GENERATE_INVOICE_PDF, {"invoice_id": str(invoice.id)})
    await refresh_storefronts(tenant.id)
    return invoice
