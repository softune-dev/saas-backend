"""The monthly limit on storefront orders (50 on Free, see app/plans.py).

WHY A STORED COUNTER. Counting rows in `orders` would let a merchant delete
orders, or the whole site, to get another 50. `tenant_order_months` hangs off
the tenant, so nothing a merchant can delete touches it.

TWO WAYS TO COUNT, on purpose:

- A LIMITED plan claims its place inside the order's own transaction
  (claim_for_order). The upsert locks the tenant's counter row until that
  transaction ends, so two shoppers at 49 cannot both become number 50, and an
  order that fails to save gives its place back by rolling back.

- An UNLIMITED plan is counted after its order is committed and answered
  (after_order), in a session of its own. The number only matters if the
  merchant later drops to Free, so it must never cost a paying merchant
  anything: no statement inside the checkout transaction, no extra row lock
  held while stock rows are locked, and a failing counter cannot fail an order
  that is already saved. A savepoint would be as safe but adds two round trips
  inside those locks. The price is that a crash in the instant between the
  order and its count loses one count.

Months are calendar months in Bangladesh, where the merchants are.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, notifications, plan_gate, plans, queue
from app.config import settings
from app.db import SessionLocal
from app.models import Site, TenantOrderMonth
from app.whatsapp import to_whatsapp_number

log = logging.getLogger(__name__)

# Bangladesh time. No DST, so a fixed offset is exact (same as app/api/pay.py).
_DHAKA = timedelta(hours=6)


@dataclass(frozen=True)
class TenantPlan:
    """All this module needs of a tenant: whose counter, and which limit. Any
    object with these two attributes (a Tenant row) works as well."""

    id: uuid.UUID
    plan: str


@dataclass(frozen=True)
class Claim:
    """What after_order needs once the order is saved. Plain values only: by
    then the request's session, and every row loaded by it, is gone."""

    tenant: TenantPlan
    site_id: uuid.UUID
    # This order's number in the month. None on an unlimited plan, which has
    # not been counted yet.
    count: int | None


async def load_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> TenantPlan:
    return TenantPlan(tenant_id, await plan_gate.tenant_plan(db, tenant_id))


# ---------------------------------------------------------------------------
#  The month
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """The only clock in this module, so a test can move it."""
    return datetime.now(UTC)


def _month_of(moment: datetime) -> date:
    return (moment + _DHAKA).date().replace(day=1)


def current_month() -> date:
    return _month_of(_now())


def _seconds_left_in_month(moment: datetime) -> int:
    local = moment + _DHAKA
    # Day 1 + 32 days always lands in the next month, whatever the length.
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (start + timedelta(days=32)).replace(day=1)
    return max(1, int((following - local).total_seconds()))


# ---------------------------------------------------------------------------
#  The counter
# ---------------------------------------------------------------------------


async def claim(db: AsyncSession, tenant) -> int | None:
    """Take one order's place in this month. Returns its number, or None when
    the plan's limit is already used up. Commits nothing: the caller's
    transaction decides whether the place is kept.

    One statement, so there is no gap between checking and counting. When the
    row is locked by another order, Postgres waits and then re-checks the WHERE
    against the committed value.
    """
    limit = plans.get_plan(tenant.plan).orders_per_month
    stmt = (
        insert(TenantOrderMonth)
        .values(tenant_id=tenant.id, month=current_month(), orders=1)
        .on_conflict_do_update(
            index_elements=["tenant_id", "month"],
            set_={"orders": TenantOrderMonth.orders + 1},
            where=None if limit is None else TenantOrderMonth.orders < limit,
        )
        .returning(TenantOrderMonth.orders)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def record_blocked(tenant_id: uuid.UUID) -> None:
    """Count a shopper who was turned away, for the merchant to see.

    Its own session because the order's transaction has been rolled back. Call
    it only after that rollback: the pool is small, and one request must not
    hold two connections. Best-effort: it only feeds a number on a screen.
    """
    try:
        async with SessionLocal() as db:
            await db.execute(
                insert(TenantOrderMonth)
                .values(tenant_id=tenant_id, month=current_month(), blocked=1)
                .on_conflict_do_update(
                    index_elements=["tenant_id", "month"],
                    set_={"blocked": TenantOrderMonth.blocked + 1},
                )
            )
            await db.commit()
    except Exception:
        log.exception("order limit: could not record a blocked order for tenant %s", tenant_id)


async def is_open(db: AsyncSession, tenant, *, month: date | None = None) -> bool:
    """May this tenant's stores take another order this month?"""
    limit = plans.get_plan(tenant.plan).orders_per_month
    if limit is None:
        return True
    used = (
        await db.execute(
            select(TenantOrderMonth.orders).where(
                TenantOrderMonth.tenant_id == tenant.id,
                TenantOrderMonth.month == (month or current_month()),
            )
        )
    ).scalar_one_or_none()
    return (used or 0) < limit


# ---------------------------------------------------------------------------
#  Checkout (called by app/api/public.py's create_public_order)
# ---------------------------------------------------------------------------


def store_whatsapp(business: dict | None) -> str | None:
    """The store's WhatsApp number as wa.me wants it ("8801..."), or None.
    Falls back to the phone, as the storefront assistant does: sign-up asks for
    one number and stores it there."""
    business = business or {}
    for key in ("whatsapp", "phone"):
        if number := to_whatsapp_number(str(business.get(key) or "")):
            return number
    return None


async def claim_for_order(db: AsyncSession, tenant: TenantPlan, site: Site) -> Claim:
    """Claim a place for the order being built, or refuse it with a 403.

    Must be the last thing before the order is saved: from here to the commit
    every other order of this tenant waits, so nothing slow may run in between.
    """
    if plans.get_plan(tenant.plan).orders_per_month is None:
        return Claim(tenant, site.id, None)

    count = await claim(db, tenant)
    if count is not None:
        return Claim(tenant, site.id, count)

    # Read before the rollback, which expires every loaded row.
    whatsapp = store_whatsapp(site.business)
    # Gives back the new customer and the order number, and releases the
    # connection before record_blocked takes one.
    await db.rollback()
    await record_blocked(tenant.id)
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, {"code": "order_limit", "whatsapp": whatsapp}
    )


async def after_order(claimed: Claim) -> None:
    """Runs as a background task once the order is committed and the shopper
    has their answer. Nothing here can fail or slow an order."""
    # ACTIVITY HOOK (free plan task 5): a received storefront order also
    # refreshes tenants.last_active_at. Call app/activity.py's touch for
    # claimed.tenant.id here; it runs for every plan and after the commit.
    limit = plans.get_plan(claimed.tenant.plan).orders_per_month
    if limit is None:
        await _count_unlimited(claimed.tenant)
    # Exact equality: a committed number is handed out once per month, so each
    # of these is true for one order only.
    elif claimed.count == limit:
        await _close_storefronts(claimed.tenant.id)
        await _tell_merchant(claimed, limit)
    elif claimed.count == limit * 8 // 10:
        await _tell_merchant(claimed, limit)


async def _count_unlimited(tenant: TenantPlan) -> None:
    try:
        async with SessionLocal() as db:
            await claim(db, tenant)
            await db.commit()
    except Exception:
        log.exception("order limit: could not count an order for tenant %s", tenant.id)


async def _close_storefronts(tenant_id: uuid.UUID) -> None:
    """Show WhatsApp ordering now rather than when the caches expire. Every
    site of the tenant: the limit is per tenant, and one that dropped from a
    bigger plan may still have two."""
    try:
        async with SessionLocal() as db:
            sites, _ = await crud.list_scoped(db, Site, tenant_id)
        for site in sites:
            await cache.invalidate_site(site.subdomain, site.custom_domain)
            await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})
    except Exception:
        log.exception("order limit: could not refresh the stores of tenant %s", tenant_id)


async def _tell_merchant(claimed: Claim, limit: int) -> None:
    left = limit - claimed.count
    body = (
        f"You have {left} orders left until the 1st. Upgrade to take unlimited orders."
        if left
        else "Shoppers now order on WhatsApp until the 1st. "
        "Upgrade to take unlimited orders in your store."
    )
    try:
        async with SessionLocal() as db:
            await notifications.notify(
                db,
                tenant_id=claimed.tenant.id,
                site_id=claimed.site_id,
                type="order_limit",
                title=f"You have used {claimed.count} of {limit} orders this month",
                body=body,
                link="/settings/billing",
            )
    except Exception:
        log.exception("order limit: could not notify tenant %s", claimed.tenant.id)


# ---------------------------------------------------------------------------
#  Site config (called by app/api/public.py's get_site_config)
# ---------------------------------------------------------------------------


async def storefront_flags(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[dict, int | None]:
    """What the plan changes on the storefront, and how long that may be cached
    (None = the usual time). Display only: the order endpoint decides live."""
    # One reading of the clock, so the answer and its cache time cannot fall on
    # different sides of midnight.
    now = _now()
    tenant = await load_tenant(db, tenant_id)
    open_now = await is_open(db, tenant, month=_month_of(now))
    flags = {
        "ordering": "checkout" if open_now else "whatsapp",
        "badge": not plans.has_feature(tenant.plan, "no_badge"),
    }
    # A store closed on the 31st must not still look closed on the 1st.
    ttl = None if open_now else min(settings.cache_ttl_seconds, _seconds_left_in_month(now))
    return flags, ttl
