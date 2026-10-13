"""The permanent marketing contact list (migrations/077).

Every signup is remembered here, and the record outlives the account: the
trial sweep, the abandoned-account sweep and the superadmin "delete tenant"
button all erase a tenant and everything that cascades from it, but this table
has no foreign keys, so the person stays reachable.

HOW IT STAYS CURRENT
  - create_tenant_owner_and_site calls upsert_tenant() right after a signup.
  - Every code path that deletes tenants calls upsert_tenants(..., deleted=True)
    FIRST, so the contact is saved with its final details and marked
    churned / former_customer before the rows disappear.
  - A background loop (app/worker.py) runs sync_all() every few hours, which
    picks up plan changes, new phone numbers and demo-page leads.

An email is one contact however many times it signs up. unsubscribed_at is
never touched by any of this: once someone opts out, a sync cannot opt them
back in.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DemoAccessRequest, Invoice, MarketingContact, Site, Tenant, User

log = logging.getLogger(__name__)

PAID_PLANS = ("starter", "growth", "business")
# The automated tests create real tenants with these addresses. They must never
# become marketing contacts, because contacts are kept forever.
TEST_EMAIL_DOMAIN = "@softune-test-fixtures.dev"


def lifecycle_for(plan: str, ever_paid: bool, deleted: bool) -> str:
    """Where this person is in the journey. Deleted accounts are either
    churned (never paid) or a former customer (paid at some point)."""
    if deleted:
        return "former_customer" if ever_paid else "churned"
    return "customer" if plan in PAID_PLANS else "trial"


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def _upsert(db: AsyncSession, rows: list[dict]) -> None:
    """Insert or update contacts by email. unsubscribed_at, notes, created_at
    and source are never overwritten on an existing row."""
    if not rows:
        return
    stmt = insert(MarketingContact).values(rows)
    keep = {"email", "id", "unsubscribed_at", "notes", "created_at", "source"}
    update = {
        col: stmt.excluded[col]
        for col in rows[0]
        if col not in keep
    }
    await db.execute(stmt.on_conflict_do_update(index_elements=["email"], set_=update))


async def _rows_for_tenants(
    db: AsyncSession, tenant_ids: list[uuid.UUID], *, deleted: bool
) -> list[dict]:
    """Contact rows for every real user of these tenants, merged with what we
    already remember about each email (so a deleted account keeps its richer
    older details instead of being blanked)."""
    if not tenant_ids:
        return []
    tenants = {
        t.id: t
        for t in (await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))).scalars()
    }
    users = (
        await db.execute(
            select(User).where(User.tenant_id.in_(tenant_ids), User.is_superadmin.is_not(True))
        )
    ).scalars().all()
    subdomains: dict[uuid.UUID, str] = {}
    for tid, sub in (
        await db.execute(
            select(Site.tenant_id, Site.subdomain)
            .where(Site.tenant_id.in_(tenant_ids))
            .order_by(Site.created_at)
        )
    ).all():
        subdomains.setdefault(tid, sub)
    first_paid = dict(
        (
            await db.execute(
                select(Invoice.tenant_id, func.min(Invoice.issued_at))
                .where(Invoice.tenant_id.in_(tenant_ids), Invoice.amount_cents > 0)
                .group_by(Invoice.tenant_id)
            )
        ).all()
    )
    existing = {
        c.email.lower(): c
        for c in (
            await db.execute(
                select(MarketingContact).where(
                    func.lower(MarketingContact.email).in_([u.email.lower() for u in users])
                )
            )
        ).scalars()
    }

    now = datetime.now(UTC)
    rows: list[dict] = []
    for u in users:
        t = tenants.get(u.tenant_id)
        if t is None or t.plan == "demo" or u.email.lower().endswith(TEST_EMAIL_DOMAIN):
            continue
        old = existing.get(u.email.lower())
        paid_at = first_paid.get(t.id)
        ever_paid = t.plan in PAID_PLANS or paid_at is not None or bool(old and old.ever_paid)
        rows.append(
            {
                "email": u.email.lower(),
                "full_name": _clean(u.full_name) or (old.full_name if old else None),
                "phone": _clean(u.phone) or (old.phone if old else None),
                "shop_name": _clean(t.name) or (old.shop_name if old else None),
                "subdomain": subdomains.get(t.id) or (old.subdomain if old else None),
                "plan": t.plan,
                "lifecycle": lifecycle_for(t.plan, ever_paid, deleted),
                "ever_paid": ever_paid,
                "tenant_id": t.id,
                "signed_up_at": (old.signed_up_at if old and old.signed_up_at else u.created_at),
                "converted_at": paid_at or (old.converted_at if old else None),
                "account_deleted_at": (
                    (old.account_deleted_at if old and old.account_deleted_at else now) if deleted else None
                ),
                "last_login_at": u.last_login_at or (old.last_login_at if old else None),
                "source": "signup",
            }
        )
    return rows


async def upsert_tenants(db: AsyncSession, tenant_ids: list[uuid.UUID], *, deleted: bool = False) -> int:
    """Save (or refresh) the contacts of these tenants. With deleted=True the
    contacts are marked churned / former_customer; call it BEFORE the delete.
    Commits. Raises on failure: callers that are about to delete data must let
    that stop the delete rather than risk losing the contact."""
    rows = await _rows_for_tenants(db, list(tenant_ids), deleted=deleted)
    await _upsert(db, rows)
    await db.commit()
    return len(rows)


async def upsert_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Best-effort capture right after a signup. A failure is logged and
    swallowed (rule 9): the daily sync will pick the person up anyway."""
    try:
        await upsert_tenants(db, [tenant_id])
    except Exception:  # noqa: BLE001
        await db.rollback()
        log.warning("couldn't save marketing contact for tenant %s", tenant_id, exc_info=True)


async def sync_all(db: AsyncSession) -> dict[str, int]:
    """Refresh every live account and pull in demo-page leads. Safe to run
    repeatedly; the worker does so every few hours and the admin can trigger it."""
    tenant_ids = [
        tid
        for (tid,) in (await db.execute(select(Tenant.id).where(Tenant.plan != "demo"))).all()
    ]
    accounts = 0
    for i in range(0, len(tenant_ids), 200):
        accounts += await upsert_tenants(db, tenant_ids[i : i + 200])

    leads = 0
    demo_rows = (
        await db.execute(select(DemoAccessRequest.email, DemoAccessRequest.first_requested_at))
    ).all()
    if demo_rows:
        stmt = insert(MarketingContact).values(
            [
                {"email": e.lower(), "source": "demo", "lifecycle": "lead", "signed_up_at": at}
                for e, at in demo_rows
            ]
        )
        # Existing contacts (a lead who later signed up) are left exactly as they are.
        result = await db.execute(stmt.on_conflict_do_nothing(index_elements=["email"]))
        leads = result.rowcount or 0
        await db.commit()
    return {"accounts": accounts, "new_leads": leads}
