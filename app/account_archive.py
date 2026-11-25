"""What we keep when an account or a store is deleted.

The owner's decision: the store and its files go, but the person's details and
everything that went into building the store stay, so they can be used again if
that person comes back.

KEPT: the account, its users, each store's settings (name, theme, contact
details, About, FAQs, legal pages, SEO, delivery, assistant), pages, categories,
products and sale events, and how many orders and customers there were.

NOT KEPT: orders and customer records (they are the shoppers' personal data, not
the merchant's), and anything secret: password and code hashes, tokens, and the
courier, payment and marketing connections. Uploaded images are deleted with the
store, so saved image links stop working.

`archive` only adds a row to the caller's transaction and never commits: the
caller deletes in that same transaction, so there is never a deletion without
its record, nor a record for something that was not deleted.
"""

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import contacts, plans
from app.models import Category, Customer, DeletedAccount, Event, Order, Product, Site, SitePage, Tenant, User

# Any column, or any key inside a JSON column, whose name looks secret is dropped.
_SECRET = re.compile(r"password|otp|token|secret|hash|credential|api_?key|private_?key", re.IGNORECASE)

# Rows per list in one record. A paid store deleted by hand can hold thousands of
# products; the record says when it had to stop.
MAX_ROWS = 2000


def _clean(value: Any) -> Any:
    """JSON-safe, with secret-looking keys removed at every depth."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if not _SECRET.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value


def _row(row: Any) -> dict:
    return _clean({attr.key: getattr(row, attr.key) for attr in inspect(row).mapper.column_attrs})


async def _rows(db: AsyncSession, model: Any, tenant_id: uuid.UUID, *where: Any) -> list[dict]:
    stmt = select(model).where(model.tenant_id == tenant_id, *where).order_by(model.created_at).limit(MAX_ROWS)
    return [_row(r) for r in (await db.execute(stmt)).scalars()]


async def _count(db: AsyncSession, model: Any, tenant_id: uuid.UUID, *where: Any) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *where))).scalar_one()


async def archive(db: AsyncSession, tenant_id: uuid.UUID, reason: str, *, site_id: uuid.UUID | None = None) -> None:
    """Saves the record for a tenant about to be deleted, or, with `site_id`,
    for one of its stores. Raises if it cannot: the caller must then delete
    nothing."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        return
    users = (await db.execute(select(User).where(User.tenant_id == tenant_id).order_by(User.created_at))).scalars().all()
    owner = next((u for u in users if u.role == "owner"), users[0] if users else None)

    stmt = select(Site).where(Site.tenant_id == tenant_id).order_by(Site.created_at)
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)
    sites = (await db.execute(stmt)).scalars().all()

    stores = []
    for site in sites:
        stores.append(
            {
                "site": _row(site),
                "template_key": site.template.key if site.template else None,
                "pages": await _rows(db, SitePage, tenant_id, SitePage.site_id == site.id),
                "categories": await _rows(db, Category, tenant_id, Category.site_id == site.id),
                "products": await _rows(db, Product, tenant_id, Product.site_id == site.id),
                "events": await _rows(db, Event, tenant_id, Event.site_id == site.id),
                "counts": {
                    "products": await _count(db, Product, tenant_id, Product.site_id == site.id),
                    "orders": await _count(db, Order, tenant_id, Order.site_id == site.id),
                },
            }
        )

    ever_paid = tenant.plan in plans.PAID_PLANS or bool(await contacts.paid_before(db, [tenant.id]))
    first = sites[0] if sites else None
    db.add(
        DeletedAccount(
            tenant_id=tenant.id,
            reason=reason,
            email=owner.email.lower() if owner else None,
            full_name=owner.full_name if owner else None,
            phone=owner.phone if owner else None,
            shop_name=(first.name if site_id is not None and first else tenant.name),
            subdomain=first.subdomain if first else None,
            custom_domain=first.custom_domain if first else None,
            plan=tenant.plan,
            ever_paid=ever_paid,
            snapshot={
                "version": 1,
                "account": _row(tenant),
                "users": [_row(u) for u in users],
                "customer_count": await _count(db, Customer, tenant_id),
                "stores": stores,
            },
        )
    )
    await db.flush()
