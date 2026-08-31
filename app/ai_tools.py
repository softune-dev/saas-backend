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
from datetime import UTC, date, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, media
from app.config import settings
from app.models import Order, OrderItem, Product, Site, Tenant

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
            "Everything set in Site Settings: domain (subdomain, connected "
            "custom domain, published status), business/contact details, "
            "full SEO configuration (meta description, keywords, OG title/"
            "description/image, favicon, indexing/sitemap, Analytics/Search "
            "Console/Pixel connection status), delivery locations, FAQs, "
            "whether About Us has been written, and whether a privacy policy "
            "or terms page is published. Call this for any question about "
            "the store's domain, contact info, SEO, shipping areas, FAQs, "
            "About Us, or legal pages — and to spot real gaps worth flagging "
            "(an empty meta description, no OG image, no domain connected)."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_media_stats",
        "description": (
            "How many images/videos the merchant has uploaded (by category: "
            "hero, products, categories, other), and total storage used "
            "against their plan's limit. Call this for any question about "
            "media/storage usage, or to check if they're close to their "
            "plan's storage limit before suggesting more uploads."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_billing_status",
        "description": (
            "The merchant's current plan, today's AI assistant usage against "
            "the plan's daily cap, and storage usage against the plan's "
            "limit. Call this for any question about their plan, billing, or "
            "usage limits. No pricing figures are available here — if asked "
            "about upgrade cost, say you don't have real pricing to quote and "
            "point them to the Billing page or support."
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
    """Everything set in Site Settings — domain, business/contact info, full
    SEO configuration, delivery locations, FAQs, and legal-page publish
    status. Every field is returned even when empty (None / False), on
    purpose — an empty meta_description or a missing OG image is exactly
    the kind of real, fixable gap the assistant should be able to notice
    and point out, not silently omit. Legal page BODY text is deliberately
    excluded (could be long; a merchant asking "what does my privacy policy
    say" is rare enough not to pay for it on every call) — only whether one
    is published, so the assistant can at least say correctly whether the
    page exists.
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    business = site.business or {}
    seo = site.seo or {}
    legal = site.legal or {}
    about = site.about or {}
    currency = await _tenant_currency(db, tenant_id)

    return {
        "site_name": site.name,
        "site_status": site.status,
        "subdomain": f"{site.subdomain}.{settings.site_base_domain}",
        "custom_domain": site.custom_domain,
        "published": site.status == "published",
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
        "og_title": seo.get("og_title") or None,
        "og_description": seo.get("og_description") or None,
        "og_image_set": bool(seo.get("og_image")),
        "favicon_set": bool(seo.get("favicon")),
        # indexing_hidden=True means noindex — deliberately named for what a
        # merchant is trying to decide ("should Google find this yet"), not
        # a raw DB flag name.
        "indexing_hidden": bool(seo.get("noindex")),
        "sitemap_enabled": seo.get("sitemap_enabled", True),
        "google_analytics_connected": bool(seo.get("google_analytics")),
        "search_console_connected": bool(seo.get("google_search_console")),
        "facebook_pixel_connected": bool(seo.get("facebook_pixel")),
        "about_us_written": bool(about.get("paragraphs")),
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


async def _get_media_stats(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    plan = tenant.plan if tenant else "starter"

    def collect() -> dict:
        # media.list_images is a synchronous Cloudinary SDK call — run off
        # the event loop, same fix as app/api/media.py's upload/cleanup
        # endpoints (see that file for why this matters on a single-worker
        # process: a blocking call here would stall every other request).
        by_category: dict[str, dict] = {}
        total_bytes = 0
        for category in sorted(media.VALID_CATEGORIES):
            images = media.list_images(site.subdomain, category)
            count = len(images)
            bytes_used = sum((img.get("bytes") or 0) for img in images)
            by_category[category] = {"count": count, "bytes": bytes_used}
            total_bytes += bytes_used
        return {"by_category": by_category, "total_bytes": total_bytes}

    try:
        stats = await run_in_threadpool(collect)
    except Exception as exc:  # noqa: BLE001 — Cloudinary being unreachable shouldn't break the chat
        log.warning("get_media_stats: media lookup failed: %s", exc)
        return {"error": "Couldn't reach media storage right now."}

    limit_bytes = media.plan_storage_limit(plan)
    return {
        "plan": plan,
        "total_files": sum(c["count"] for c in stats["by_category"].values()),
        "by_category": {
            cat: {"files": c["count"], "megabytes": round(c["bytes"] / 1_048_576, 1)}
            for cat, c in stats["by_category"].items()
        },
        "storage_used_mb": round(stats["total_bytes"] / 1_048_576, 1),
        "storage_limit_mb": round(limit_bytes / 1_048_576, 1),
        "storage_percent_used": round(stats["total_bytes"] / limit_bytes * 100, 1)
        if limit_bytes
        else 0,
    }


# Mirrors app/ai.py's PLAN_AI_DAILY_CAP — duplicated, not imported, since
# app/ai.py imports THIS module (ai_tools), so importing back would be
# circular. Same tradeoff this codebase already makes between app/media.py's
# and app/ai.py's own separate per-plan dicts — keep in sync if it changes.
_PLAN_AI_DAILY_CAP: dict[str, int] = {"demo": 50, "starter": 15, "growth": 80, "business": 250}
_DEFAULT_AI_DAILY_CAP = 80


async def _get_billing_status(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        return {"error": "No account found."}
    plan = tenant.plan
    ai_cap = _PLAN_AI_DAILY_CAP.get(plan, _DEFAULT_AI_DAILY_CAP)

    # Same cache key format + raw-integer-counter shape as app/ai.py's
    # _usage_key/get_usage (client.incr writes a plain int, not JSON).
    ai_usage_key = f"ai:suggestions:{tenant_id}:{date.today().isoformat()}"
    ai_used = 0
    try:
        raw = await cache.client().get(ai_usage_key)
        ai_used = int(raw) if raw else 0
    except Exception as exc:  # noqa: BLE001 — a display glitch, not a hard failure
        log.warning("get_billing_status: AI usage lookup failed: %s", exc)

    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    storage_used_mb = 0.0
    storage_limit_mb = round(media.plan_storage_limit(plan) / 1_048_576, 1)
    if site is not None:
        try:
            used_bytes = await run_in_threadpool(media.site_storage_used_bytes, site.subdomain)
            storage_used_mb = round(used_bytes / 1_048_576, 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_billing_status: media lookup failed: %s", exc)

    return {
        "plan": plan,
        "account_status": tenant.status,
        "ai_requests_used_today": ai_used,
        "ai_requests_daily_limit": ai_cap,
        "storage_used_mb": storage_used_mb,
        "storage_limit_mb": storage_limit_mb,
        # No real pricing source exists in this backend (no PLAN_PRICE
        # table) — never invented here. The system prompt is told to say so
        # plainly rather than guess a number if asked about upgrade cost.
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


_ICON = {
    "stock": "/sidebar/products.svg",
    "sales": "/sidebar/analytics.svg",
    "content": "/sidebar/note.svg",
    "org": "/sidebar/categories.svg",
    "theme": "/sidebar/themes.svg",
}


async def get_suggested_prompts(
    db: AsyncSession, tenant_id: uuid.UUID, context: str = "default"
) -> list[dict]:
    """Suggestion chips shown before the merchant has typed anything — real,
    per-merchant prompts instead of a fixed "honey store" placeholder list.
    Built from the SAME read-only signals get_business_overview/get_site_info
    already expose (no separate query path to keep in sync), picked by
    priority so the most actionable gap surfaces first. Deliberately no
    Gemini call here: these are cheap DB reads, shown every time the sidebar
    opens, and must not cost the merchant AI credits or add latency just to
    render a chip they might not even click.
    """
    overview = await _get_business_overview(db, tenant_id)
    info = await _get_site_info(db, tenant_id)
    if info.get("error"):
        info = {}

    name = info.get("business_name") or overview.get("site_name") or "my store"
    description = (info.get("description") or "").strip()
    niche = description[:70] if description else name

    candidates: list[dict] = []

    if context == "theme_editor":
        if not info.get("about_us_written"):
            candidates.append({"text": f"Write an About Us story for {name}", "icon": _ICON["content"]})
        candidates.append(
            {"text": f"Suggest a modern color palette that fits {niche}", "icon": _ICON["theme"]}
        )
        candidates.append(
            {"text": f"Write a high-converting Hero headline for {name}", "icon": _ICON["content"]}
        )
        if not info.get("faqs"):
            candidates.append({"text": "Draft FAQs based on what I sell", "icon": _ICON["content"]})
        candidates.append(
            {"text": "Recommend the best section order for my storefront", "icon": _ICON["org"]}
        )
        candidates.append({"text": "Generate an announcement banner for a sale", "icon": _ICON["sales"]})
        return candidates[:4]

    # Default (general chat sidebar) — ranked by "what's actually worth this
    # merchant's attention right now", not a generic tour of features.
    low_stock = overview.get("low_stock_products") or 0
    if low_stock > 0:
        candidates.append(
            {"text": f"Which of my {low_stock} low-stock products need restocking?", "icon": _ICON["stock"]}
        )
    if not info.get("about_us_written"):
        candidates.append({"text": f"Write an About Us story for {name}", "icon": _ICON["content"]})
    if not info.get("faqs"):
        candidates.append({"text": "Draft FAQs based on what I sell", "icon": _ICON["content"]})
    if not info.get("meta_description"):
        candidates.append({"text": "Write an SEO meta description for my store", "icon": _ICON["content"]})
    if (overview.get("orders_last_30_days") or 0) == 0 and (overview.get("total_products") or 0) > 0:
        candidates.append({"text": "Suggest ways to get my first sales this month", "icon": _ICON["sales"]})
    if not info.get("custom_domain"):
        candidates.append({"text": "How do I connect my own domain to my store?", "icon": _ICON["org"]})
    if 0 < (overview.get("total_products") or 0) < 5:
        candidates.append({"text": "Help me write descriptions for a few products", "icon": _ICON["stock"]})

    # Always-available fallbacks, personalized, appended only to fill out to
    # 4 — skipped if a gap-based suggestion above already covers that same
    # topic (same icon category), so a healthy store doesn't see "check low
    # inventory" twice just phrased two different ways.
    used_icons = {c["icon"] for c in candidates}
    for fallback in (
        {"text": "Analyze my store's sales & conversion trends", "icon": _ICON["sales"]},
        {"text": f"Suggest modern theme styles for {niche}", "icon": _ICON["theme"]},
        {"text": "Check which products are low on inventory", "icon": _ICON["stock"]},
    ):
        if len(candidates) >= 4:
            break
        if fallback["icon"] in used_icons:
            continue
        candidates.append(fallback)
        used_icons.add(fallback["icon"])

    return candidates[:4]


_HANDLERS = {
    "get_business_overview": _get_business_overview,
    "list_products": _list_products,
    "list_orders": _list_orders,
    "get_order": _get_order,
    "get_sales_summary": _get_sales_summary,
    "get_site_info": _get_site_info,
    "get_media_stats": _get_media_stats,
    "get_billing_status": _get_billing_status,
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
