"""Generic tenant-scoped database helpers.

TWO JOBS:

1. LESS CODE. Five functions here replace the ~200 lines of near-identical
   SELECT/INSERT/UPDATE/DELETE you would otherwise write per resource. The
   commerce router uses them for categories, products AND orders.

2. SECURITY, BY CONSTRUCTION. Every read and write helper *requires* a
   tenant_id argument and puts it in the WHERE clause. There is no
   "get by id" that skips it. That is the whole tenant-isolation model: you
   cannot forget the filter, because the function will not run without it.

   Never bypass these with a raw `select(Model).where(Model.id == x)` in a
   router. That is exactly the line of code that leaks customer data.
"""

import asyncio
import re
import uuid
from typing import Any, Callable, TypeVar

import asyncpg.exceptions
from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base

T = TypeVar("T", bound=Base)
R = TypeVar("R")


async def _retry_on_pooler_error(fn: Callable[[], R], max_attempts: int = 3) -> R:
    """Retry a database operation up to 3 times if it hits a pgbouncer pooler error.

    Pgbouncer's transaction pooler occasionally kills idle connections between
    requests, causing prepared statements to become invalid. This is harmless on
    retry — just wait a tiny bit and try again.
    """
    for attempt in range(max_attempts):
        try:
            return await fn()
        except asyncpg.exceptions.InvalidSQLStatementNameError:
            if attempt == max_attempts - 1:
                raise
            await asyncio.sleep(0.1 * (2**attempt))  # backoff: 0.1s, 0.2s, 0.4s


# ---------------------------------------------------------------------------
#  Reads
# ---------------------------------------------------------------------------


async def get_scoped(
    db: AsyncSession, model: type[T], tenant_id: uuid.UUID, obj_id: uuid.UUID
) -> T:
    """Fetch one row by id, but only if it belongs to this tenant.

    Returns 404 — not 403 — when the row exists but belongs to someone else.
    That is deliberate: a 403 would confirm the id is real, letting an attacker
    enumerate other tenants' record ids. "Not found" leaks nothing.
    """
    stmt = select(model).where(model.id == obj_id, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj


async def list_scoped(
    db: AsyncSession,
    model: type[T],
    tenant_id: uuid.UUID,
    *,
    filters: list | None = None,
    order_by: Any = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[T], int]:
    """Paginated, tenant-scoped list. Returns (rows, total_count).

    The count is a second query rather than a window function. Simpler, and the
    planner can often answer it from an index alone.
    """
    where = [model.tenant_id == tenant_id, *(filters or [])]  # type: ignore[attr-defined]

    total = (
        await db.execute(select(func.count()).select_from(model).where(*where))
    ).scalar_one()

    stmt = select(model).where(*where).limit(limit).offset(offset)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


# ---------------------------------------------------------------------------
#  Writes
# ---------------------------------------------------------------------------


async def save(db: AsyncSession, obj: T) -> T:
    """INSERT or UPDATE, translating database constraint errors into clean 409s.

    Without this translation a duplicate slug surfaces as a 500 plus a wall of
    asyncpg traceback. With it, the client gets an actionable message and you get
    a clean log.
    """
    db.add(obj)
    try:
        await _retry_on_pooler_error(db.commit)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _explain(exc)) from exc
    await _retry_on_pooler_error(lambda: db.refresh(obj))
    return obj


async def delete(db: AsyncSession, obj: T) -> None:
    await db.delete(obj)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Hit when ON DELETE RESTRICT blocks it — e.g. deleting a template that
        # live sites still use. That refusal is the database protecting you.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot delete: other records still depend on this one.",
        ) from exc


def apply_updates(obj: T, data: dict) -> T:
    """Copy only the fields the client actually sent onto the model.

    Pydantic's `exclude_unset=True` gives us "sent" vs "omitted", so a PATCH that
    omits a field leaves it alone instead of nulling it.
    """
    for key, value in data.items():
        setattr(obj, key, value)
    return obj


def _explain(exc: IntegrityError) -> str:
    """Turn a Postgres constraint name into something a human can act on."""
    detail = str(getattr(exc, "orig", exc))
    known = {
        "uq_site_pages_site_slug": "A page with that URL already exists on this site.",
        "uq_products_site_slug": "A product with that URL slug already exists.",
        "uq_products_site_sku": "That SKU is already used on this site.",
        "uq_categories_site_slug": "A category with that URL slug already exists.",
        "uq_orders_site_number": "That order number already exists.",
        "sites_subdomain_key": "That subdomain is taken. Try another.",
        "uq_sites_custom_domain": "That domain is already connected to another site.",
        "users_email_key": "An account with that email already exists.",
        "tenants_slug_key": "That workspace name is taken.",
        "uq_courier_connections_site_provider": "This site already has a connection for that courier. Disconnect it first.",
        "uq_payment_connections_site_provider": "This site already has a connection for that payment method. Disconnect it first.",
        "uq_fraud_blocklist_site_phone": "That phone number is already on the blocklist.",
        "uq_push_subscriptions_endpoint": "This browser is already subscribed.",
    }
    for name, message in known.items():
        if name in detail:
            return message
    if "foreign key" in detail.lower():
        return "Referenced record does not exist."
    return "That value conflicts with an existing record."


# ---------------------------------------------------------------------------
#  Small utilities
# ---------------------------------------------------------------------------


def slugify(text: str, fallback: str = "item") -> str:
    """URL-safe slug. Deliberately boring — no unicode transliteration library."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback


async def next_order_number(db: AsyncSession, site_id: uuid.UUID) -> str:
    """Sequential per-site order reference, e.g. ORD-1001.

    Atomic `UPDATE ... RETURNING` against order_counters (one row per site,
    see migrations/022_order_counters.sql) — O(1) regardless of how many
    orders the site already has. The previous implementation counted
    existing rows on every call, which meant checkout got slower as a site's
    order history grew, and wasn't even race-free under concurrent checkouts
    (two simultaneous orders could compute the same count). The UPDATE takes
    a row lock, so two concurrent callers can never get the same number.
    """
    from app.models import OrderCounter

    row = (
        await db.execute(
            update(OrderCounter)
            .where(OrderCounter.site_id == site_id)
            .values(next_number=OrderCounter.next_number + 1)
            .returning(OrderCounter.next_number)
        )
    ).scalar_one_or_none()

    if row is None:
        # Site predates order_counters and somehow missed the backfill (or
        # the create_site insert) — self-heal by seeding it now. Extremely
        # rare path; a tiny race window here is an acceptable trade for not
        # complicating the common path with upsert logic.
        db.add(OrderCounter(site_id=site_id, next_number=1001))
        await db.flush()
        row = 1001

    return f"ORD-{row}"
