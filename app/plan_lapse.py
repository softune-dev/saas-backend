"""A paid plan that ends drops to Free, and comes back whole when paid again.

Nothing is deleted. The store keeps running with Free's limits: the best
selling products stay on it and the others are kept but hidden, and a custom
domain keeps working for a while before the store returns to its subdomain.

The automatic drop only happens once FREE_PLAN_ENABLED is on (app/worker.py).
Until then an unpaid account is paused as before. The functions here are also
what a manual plan change uses (app/api/superadmin.py).

WHICH PRODUCTS STAY. The tenant's active products by units ever sold, newest
first among equals. Recomputed whenever a lapsed store's products change, so
switching a visible one off brings the next one up, and switching on a hidden
one does not sneak past the limit.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, Update, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import cache, crud, plans, queue
from app.config import settings
from app.db import SessionLocal
from app.models import Order, OrderItem, Product, Site, Tenant

log = logging.getLogger(__name__)

# How long a custom domain keeps working after the plan that included it ended.
DOMAIN_GRACE = timedelta(days=30)


def _still_paid_and_late(cutoff: datetime) -> tuple:
    return (
        Tenant.plan.in_(plans.PAID_PLANS),
        # Paused accounts too: they are the ones an earlier sweep locked out.
        # Never "suspended", which is a decision a person made.
        Tenant.status.in_(("active", "payment_overdue")),
        Tenant.plan_renews_at.is_not(None),
        Tenant.plan_renews_at < cutoff,
    )


# Statements are functions so tests can check them on one tenant of their own,
# without ever running a sweep.
def candidates_stmt(cutoff: datetime) -> Select:
    return select(Tenant.id).where(*_still_paid_and_late(cutoff))


def lapse_stmt(tenant_id: uuid.UUID, cutoff: datetime) -> Update:
    """Re-checks everything: emails are sent between one tenant and the next,
    and a merchant who pays in between must keep the plan they paid for."""
    return (
        update(Tenant)
        .where(Tenant.id == tenant_id, *_still_paid_and_late(cutoff))
        .values(
            plan="free",
            status="active",
            plan_months=1,
            plan_renews_at=None,
            plan_renewal_reminded_at=None,
            plan_overdue_notified_at=None,
            plan_overdue_since=None,
            plan_deletion_warned_at=None,
            plan_lapsed_at=func.now(),
        )
        .returning(Tenant.id)
        .execution_options(synchronize_session=False)
    )


def hide_stmt(tenant_id: uuid.UUID, limit: int) -> Update:
    # An alias, so the ranking reads the whole catalogue instead of being tied
    # to the row the UPDATE is looking at.
    ranked = aliased(Product)
    sold = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .where(OrderItem.product_id == ranked.id, Order.tenant_id == tenant_id)
        .correlate(ranked)
        .scalar_subquery()
    )
    keep = (
        select(ranked.id)
        .where(ranked.tenant_id == tenant_id, ranked.is_active)
        .order_by(sold.desc(), ranked.created_at.desc(), ranked.id)
        .limit(limit)
    )
    hidden = Product.id.not_in(keep)
    return (
        update(Product)
        .where(Product.tenant_id == tenant_id, Product.plan_hidden != hidden)
        .values(plan_hidden=hidden)
        .execution_options(synchronize_session=False)
    )


async def hide_extra_products(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    limit = plans.get_plan("free").products
    await db.execute(hide_stmt(tenant_id, limit))
    await db.commit()


async def sync_if_free(tenant_id: uuid.UUID) -> None:
    """After a product was switched on or off, or deleted. Own session and best
    effort: the merchant's change is already saved and must not fail on this."""
    try:
        async with SessionLocal() as db:
            plan = (await db.execute(select(Tenant.plan).where(Tenant.id == tenant_id))).scalar_one_or_none()
            if plan != "free":
                return
            await hide_extra_products(db, tenant_id)
    except Exception:
        log.exception("could not refresh hidden products of tenant %s", tenant_id)


async def restore(tenant_id: uuid.UUID) -> None:
    """The tenant is on a paid plan again: everything comes back. A domain that
    was taken off the store is asked for again, and connects by itself if its
    DNS still points here (app/domains.py).

    Own session and never raises: it runs right after a payment was applied,
    and the invoice and confirmation must not be lost if this fails."""
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(Product)
                .where(Product.tenant_id == tenant_id, Product.plan_hidden)
                .values(plan_hidden=False)
                .execution_options(synchronize_session=False)
            )
            waiting = (
                await db.execute(
                    select(Site.id, Site.pending_custom_domain).where(
                        Site.tenant_id == tenant_id,
                        Site.status == "published",
                        Site.pending_custom_domain.is_not(None),
                    )
                )
            ).all()
            await db.commit()
        for site_id, domain in waiting:
            await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site_id), "domain": domain})
    except Exception:
        log.exception("could not restore the stores of tenant %s", tenant_id)


# ---------------------------------------------------------------------------
#  Custom domain, after the grace period
# ---------------------------------------------------------------------------


def domains_due_stmt(cutoff: datetime) -> Select:
    return (
        select(Site.id)
        .join(Tenant, Tenant.id == Site.tenant_id)
        .where(
            Tenant.plan == "free",
            Tenant.plan_lapsed_at.is_not(None),
            Tenant.plan_lapsed_at < cutoff,
            Site.custom_domain.is_not(None),
        )
    )


def release_domain_stmt(site_id: uuid.UUID, cutoff: datetime) -> Update:
    """The domain moves to the pending column, so upgrading asks for it again.
    A newer request already waiting there is kept."""
    return (
        update(Site)
        .where(Site.id == site_id, Site.id.in_(domains_due_stmt(cutoff)))
        .values(
            pending_custom_domain=func.coalesce(Site.pending_custom_domain, Site.custom_domain),
            pending_domain_requested_at=None,
            custom_domain=None,
        )
        .returning(Site.id)
        .execution_options(synchronize_session=False)
    )


async def sweep_domains() -> None:
    cutoff = datetime.now(UTC) - DOMAIN_GRACE
    async with SessionLocal() as db:
        due = (await db.execute(domains_due_stmt(cutoff))).scalars().all()
        for site_id in due:
            site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
            if site is None:
                continue
            subdomain, domain = site.subdomain, site.custom_domain
            released = (await db.execute(release_domain_stmt(site_id, cutoff))).first()
            await db.commit()
            if released is None or not domain:
                continue
            await cache.invalidate_site(subdomain, domain)
            await queue.publish(queue.JOB_DETACH_DOMAIN, {"site_id": str(site_id), "domain": domain})
            # Already attached unless the store has never used its subdomain.
            await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site_id)})
            await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site_id)})
            log.info("domain %s released from lapsed site %s", domain, site_id)


# ---------------------------------------------------------------------------
#  The drop itself
# ---------------------------------------------------------------------------


async def lapse_overdue(cutoff: datetime) -> None:
    """Called by the worker instead of pausing, once Free exists. One tenant at
    a time, committed before its email, so nobody hears of a drop that did not
    happen and one bad row cannot stop the others."""
    from app import mailer, notifications
    from app.billing_actions import refresh_storefronts
    from app.models import User

    if not settings.free_plan_enabled:
        return
    async with SessionLocal() as db:
        ids = (await db.execute(candidates_stmt(cutoff))).scalars().all()
    for tenant_id in ids:
        try:
            async with SessionLocal() as db:
                old_plan = (await db.execute(select(Tenant.plan).where(Tenant.id == tenant_id))).scalar_one_or_none()
                if (await db.execute(lapse_stmt(tenant_id, cutoff))).first() is None:
                    await db.rollback()
                    continue
                # Same transaction: a store must not be Free with everything shown.
                await db.execute(hide_stmt(tenant_id, plans.get_plan("free").products))
                await db.commit()
                await refresh_storefronts(tenant_id)
                owner = (
                    await db.execute(select(User).where(User.tenant_id == tenant_id, User.role == "owner"))
                ).scalars().first()
                site = (await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))).scalars().first()
                name = plans.get_plan(old_plan).name
                if site is not None:
                    await notifications.notify(
                        db,
                        tenant_id=tenant_id,
                        site_id=site.id,
                        type="plan_lapsed",
                        title="Your store is now on the Free plan",
                        body=f"Your {name} plan was not renewed. Everything is kept. Upgrade to bring it all back.",
                        link="/settings/billing",
                    )
                if owner is not None:
                    subject, html_body, text_body = mailer.plan_lapsed_email(
                        owner.full_name, name, plans.get_plan("free").products, DOMAIN_GRACE.days
                    )
                    await mailer.send_email(owner.email, subject, html_body, text_body)
        except Exception:  # noqa: BLE001 - the next sweep tries this tenant again
            log.exception("could not move tenant %s to Free", tenant_id)
