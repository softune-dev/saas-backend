"""Read-only business-data tools the AI assistant can call.

These are the ONLY way the assistant ever touches real data — it never gets
a database session or a raw query of its own. Every function here takes
`tenant_id` from the verified JWT (see app/api/ai.py), never from the
model's own arguments, so a tool call is exactly as tenant-isolated as any
other endpoint in this app: it can only ever see the caller's own rows.

No write tools exist. This assistant answers questions; it doesn't change
anything. If that changes later, each write tool needs its own explicit
confirmation step in the UI before it runs — not something to bolt on here
silently.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.models import Order, OrderItem, Product, Site

log = logging.getLogger(__name__)

# Gemini function-calling schema — kept in the plain JSON-schema-ish shape
# the API expects (OBJECT/STRING/INTEGER/BOOLEAN, not Python types).
TOOL_DECLARATIONS = [
    {
        "name": "get_business_overview",
        "description": (
            "High-level snapshot of the merchant's store: product counts, "
            "low-stock count, and order/revenue totals for the last 30 days "
            "and all time. Good first call for any general question about "
            "'how is my store doing'."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "list_products",
        "description": "Search or list the merchant's products, optionally filtered to low-stock items.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Optional name search, e.g. 'honey'."},
                "low_stock_only": {"type": "BOOLEAN", "description": "Only products at or below 5 units in stock."},
                "limit": {"type": "INTEGER", "description": "Max results, default 10, max 25."},
            },
        },
    },
    {
        "name": "list_orders",
        "description": "List the merchant's recent orders, optionally filtered by status (pending, paid, shipped, delivered, cancelled).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "description": "Optional order status filter."},
                "limit": {"type": "INTEGER", "description": "Max results, default 10, max 25."},
            },
        },
    },
    {
        "name": "get_order",
        "description": "Look up one order's full detail (items, total, customer, status) by its order number.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "order_number": {"type": "STRING", "description": "The order number the merchant gives you, e.g. 'ORD-1042'."},
            },
            "required": ["order_number"],
        },
    },
    {
        "name": "get_sales_summary",
        "description": "Revenue, order count, average order value, and top-selling products over a recent period.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {"type": "INTEGER", "description": "Lookback window in days, default 30, max 365."},
            },
        },
    },
    {
        "name": "get_site_info",
        "description": (
            "The merchant's business/contact details, SEO settings, delivery "
            "locations, FAQs, and whether a privacy policy or terms page is "
            "published — everything set in Site Settings. Call this for any "
            "question about the store's contact info, address, hours, socials, "
            "SEO, shipping areas, FAQs, or legal pages."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:,.2f} {currency}"


async def _tenant_currency(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    row = (
        await db.execute(
            select(Product.currency).where(Product.tenant_id == tenant_id).limit(1)
        )
    ).scalar_one_or_none()
    return row or "USD"


async def _get_business_overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    currency = await _tenant_currency(db, tenant_id)

    total_products, active_products, low_stock = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Product.is_active.is_(True)),
                func.count().filter(
                    Product.track_stock.is_(True), Product.stock <= 5, Product.is_active.is_(True)
                ),
            ).where(Product.tenant_id == tenant_id)
        )
    ).one()

    since = datetime.now(UTC) - timedelta(days=30)
    orders_30d, revenue_30d = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.tenant_id == tenant_id, Order.created_at >= since
            )
        )
    ).one()
    orders_all, revenue_all = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.tenant_id == tenant_id
            )
        )
    ).one()

    return {
        "site_name": site.name if site else None,
        "site_status": site.status if site else None,
        "total_products": total_products,
        "active_products": active_products,
        "low_stock_products": low_stock,
        "orders_last_30_days": orders_30d,
        "revenue_last_30_days": _money(revenue_30d, currency),
        "orders_all_time": orders_all,
        "revenue_all_time": _money(revenue_all, currency),
    }


async def _get_site_info(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Everything set in Site Settings — business/contact info, SEO basics,
    delivery locations, FAQs, and legal-page publish status. Legal page BODY
    text is deliberately excluded (could be long; a merchant asking "what
    does my privacy policy say" is rare enough not to pay for it on every
    call) — only whether one is published, so the assistant can at least
    say correctly whether the page exists.
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    business = site.business or {}
    seo = site.seo or {}
    legal = site.legal or {}
    currency = await _tenant_currency(db, tenant_id)

    return {
        "site_name": site.name,
        "business_name": business.get("name") or None,
        "description": business.get("description") or None,
        "phone": business.get("phone") or None,
        "whatsapp": business.get("whatsapp") or None,
        "email": business.get("email") or None,
        "address": business.get("address") or None,
        "opening_hours": business.get("opening_hours") or [],
        "socials": business.get("socials") or {},
        "support_note": business.get("support_note") or None,
        "seo_title_suffix": seo.get("title_suffix") or None,
        "meta_description": seo.get("meta_description") or None,
        "keywords": seo.get("keywords") or None,
        "delivery_locations": [
            {"name": loc.get("name"), "charge": _money(loc.get("charge_cents", 0), currency)}
            for loc in (site.shipping or {}).get("locations", [])
        ],
        "faqs": [
            {"question": f.get("question"), "answer": f.get("answer")}
            for f in (site.faqs or [])
        ],
        "privacy_policy_published": bool(legal.get("privacy", {}).get("published")),
        "terms_published": bool(legal.get("terms", {}).get("published")),
    }


async def _list_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str | None = None,
    low_stock_only: bool = False,
    limit: int = 10,
) -> dict:
    limit = min(max(limit or 10, 1), 25)
    stmt = select(Product).where(Product.tenant_id == tenant_id)
    if query:
        stmt = stmt.where(Product.name.ilike(f"%{query}%"))
    if low_stock_only:
        stmt = stmt.where(Product.track_stock.is_(True), Product.stock <= 5)
    stmt = stmt.order_by(Product.updated_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "products": [
            {
                # Included so a later edit request can reference this exact
                # row via product_id instead of a fuzzy name match — see
                # app/ai_actions.py's update_product.
                "id": str(p.id),
                "name": p.name,
                "sku": p.sku,
                "price": _money(p.price_cents, p.currency),
                "stock": p.stock,
                "is_active": p.is_active,
            }
            for p in rows
        ]
    }


async def _list_orders(
    db: AsyncSession, tenant_id: uuid.UUID, status: str | None = None, limit: int = 10
) -> dict:
    limit = min(max(limit or 10, 1), 25)
    stmt = select(Order).where(Order.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Order.status == status)
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "orders": [
            {
                "order_number": o.order_number,
                "status": o.status,
                "total": _money(o.total_cents, o.currency),
                "customer_name": (o.customer or {}).get("name", ""),
                "created_at": o.created_at.isoformat(),
            }
            for o in rows
        ]
    }


async def _get_order(db: AsyncSession, tenant_id: uuid.UUID, order_number: str) -> dict:
    order = (
        await db.execute(
            select(Order).where(Order.tenant_id == tenant_id, Order.order_number == order_number)
        )
    ).scalar_one_or_none()
    if order is None:
        return {"error": "No order found with that number."}
    items = (
        await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    ).scalars().all()
    return {
        "order_number": order.order_number,
        "status": order.status,
        "total": _money(order.total_cents, order.currency),
        "customer_name": (order.customer or {}).get("name", ""),
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "name": i.name_snapshot,
                "quantity": i.quantity,
                "total": _money(i.total_cents, order.currency),
            }
            for i in items
        ],
    }


async def _get_sales_summary(db: AsyncSession, tenant_id: uuid.UUID, days: int = 30) -> dict:
    days = min(max(days or 30, 1), 365)
    since = datetime.now(UTC) - timedelta(days=days)
    currency = await _tenant_currency(db, tenant_id)

    count, revenue = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.tenant_id == tenant_id, Order.created_at >= since
            )
        )
    ).one()

    top_rows = (
        await db.execute(
            select(OrderItem.name_snapshot, func.sum(OrderItem.quantity).label("qty"))
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.tenant_id == tenant_id, Order.created_at >= since)
            .group_by(OrderItem.name_snapshot)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(5)
        )
    ).all()

    return {
        "days": days,
        "order_count": count,
        "revenue": _money(revenue, currency),
        "average_order": _money(int(revenue / count) if count else 0, currency),
        "top_products": [{"name": name, "quantity": int(qty)} for name, qty in top_rows],
    }


_HANDLERS = {
    "get_business_overview": _get_business_overview,
    "list_products": _list_products,
    "list_orders": _list_orders,
    "get_order": _get_order,
    "get_sales_summary": _get_sales_summary,
    "get_site_info": _get_site_info,
}


async def execute_tool(
    name: str, args: dict, db: AsyncSession, tenant_id: uuid.UUID
) -> dict:
    """Runs one tool call. Cached briefly (60s) per tenant+tool+args so a
    multi-step question (the model calling 2-3 tools to answer one message)
    doesn't repeat a query it just made, and so a merchant re-asking a
    similar question a moment later doesn't re-hit the database — the same
    "cache what's expensive to recompute" instinct as cache.py, just scoped
    to a single chat turn's lifetime rather than a real invalidation-driven
    cache.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'."}

    cache_key = f"ai:tool:{tenant_id}:{name}:{json.dumps(args, sort_keys=True)}"
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return cached["result"]

    try:
        result = await handler(db, tenant_id, **args)
    except TypeError as exc:
        log.warning("Bad tool args for %s: %s", name, exc)
        return {"error": "Invalid arguments for that lookup."}

    await cache.set_json(cache_key, {"result": result}, ttl=60)
    return result
