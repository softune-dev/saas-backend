"""Shared plan-upgrade logic used by both a human (Superadmin's manual plan
change / confirm-renewal actions in app/api/superadmin.py) and the automated
bKash/bank SMS webhook (app/api/public.py's bkash_sms_webhook). Keeping this
in one place means an auto-verified payment produces exactly the same
tenant/invoice state a person clicking the button in Superadmin would have
produced — not a second, slightly-different code path.
"""

import calendar
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, invoices as invoices_module, queue
from app.models import Invoice, Tenant


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


async def apply_paid_plan(db: AsyncSession, tenant: Tenant, plan: str) -> Invoice:
    """Puts a tenant onto a paid plan and issues the invoice for it — covers
    both a first-time purchase/upgrade (different plan, or coming off
    trial/demo) and a same-plan renewal, same as
    app/api/superadmin.py's update_tenant + confirm_plan_renewal do by hand.

    Always advances plan_renews_at by exactly one month from wherever it
    already was (or from now, if this is the first paid cycle) — never
    "today + 1 month" recomputed from a late payment's arrival date, so a
    merchant who pays a few days late still renews on the same day next
    cycle (migrations/065).
    """
    now = datetime.now(UTC)
    tenant.plan = plan
    tenant.plan_renews_at = add_one_month(tenant.plan_renews_at or now)
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
    return invoice
