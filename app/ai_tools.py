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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, media, risk_score as risk_score_module
from app.config import settings
from app.models import (
    Category,
    CourierConnection,
    Customer,
    Event,
    FraudBlocklistEntry,
    FraudIpBlocklistEntry,
    Order,
    OrderItem,
    Product,
    Site,
    Tenant,
)

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
        "name": "get_product",
        "description": (
            "Full detail on ONE product — every field, not the short summary list_products "
            "gives you. Always call this before proposing an update_product edit, so you can "
            "show the merchant everything currently on the product before asking what to change."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "product_id": {"type": "STRING", "description": "Exact id, e.g. from an earlier list_products result."},
                "product_name": {"type": "STRING", "description": "Name or partial name, if you don't have an id yet."},
                "sku": {"type": "STRING", "description": "SKU/product code, if the merchant gave you one instead of a name (e.g. 'VEILA-DUP-009')."},
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
    {
        "name": "list_categories",
        "description": (
            "The merchant's current category list — name and how many active "
            "products are in each. Call this BEFORE proposing set_categories "
            "or add_category so you know what already exists, instead of "
            "asking the merchant to recite their own category list."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_fraud_status",
        "description": (
            "The merchant's actual configured fraud-protection setup: which "
            "of the checkout-time rules (hold first-time high-value orders, "
            "flag burst orders from one phone, block blocklisted numbers, one "
            "open order per device, cooldown after a cancelled order) are "
            "enabled and their thresholds, plus how many phone numbers and IP "
            "addresses are on the blocklists. Call this for any question "
            "about their fraud rules, blocklist, or 'is X protection on' — "
            "never guess whether a rule is enabled."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "list_events",
        "description": (
            "The merchant's promo/coupon events (there is no separate "
            "'coupon' concept — a discount campaign IS an Event): name, "
            "discount percent, whether it's currently active, whether it's "
            "the storefront popup, and how many products are bound to it. "
            "Call this BEFORE proposing create_event so you know what "
            "already exists, same reasoning as list_categories."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "search_customers",
        "description": (
            "Search the merchant's customers by phone number or name. "
            "Returns lightweight results (id, phone, name, email) — call "
            "get_customer with the id or phone for full detail (order "
            "history, total spent, risk score)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Phone number or name, partial match ok."},
                "limit": {"type": "INTEGER", "description": "Max results, default 10, max 25."},
            },
        },
    },
    {
        "name": "get_customer",
        "description": (
            "Full detail on one customer: how many orders they've placed, "
            "total spent, last order date, and their computed risk score "
            "(Low/Medium/High, with the real signals behind it — delivery "
            "success rate, confirmed fraud history, blocklisted IP). "
            "Provide EITHER phone or customer_id (customer_id from a prior "
            "search_customers call)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "phone": {"type": "STRING", "description": "The customer's exact phone number."},
                "customer_id": {"type": "STRING", "description": "A customer id from a prior search_customers call."},
            },
        },
    },
    {
        "name": "list_courier_connections",
        "description": (
            "Which courier accounts (Steadfast, Pathao, RedX, eCourier) this "
            "site has connected: provider, connection status, and label. "
            "Never returns API keys or webhook secrets — call this for any "
            "question about courier connection status, not to check "
            "credentials."
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
        stmt = stmt.where(or_(Product.name.ilike(f"%{query}%"), Product.sku.ilike(f"%{query}%")))
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


async def _get_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: str | None = None,
    product_name: str | None = None,
    sku: str | None = None,
) -> dict:
    """Every field on one product — the read side of the "show the merchant
    everything, then ask what to change" edit flow (see app/ai.py's system
    prompt and app/ai_forms.py's update_product diff, which resolves the
    SAME product by the same id-or-name rule right before a confirm so the
    two never disagree about which row is being discussed).

    `product_name` and `sku` both search BOTH the name and sku columns —
    not just their own field — because a merchant giving a SKU like
    "VEILA-DUP-009" often gets typed into whichever single free-text slot
    the model reaches for first, and there's no real cost to matching a
    SKU-shaped string against the name column too (it just won't hit).
    """
    product = None
    if product_id:
        try:
            pid = uuid.UUID(product_id)
        except ValueError:
            return {"error": "That doesn't look like a valid product id."}
        product = (
            await db.execute(
                select(Product).where(Product.id == pid, Product.tenant_id == tenant_id)
            )
        ).scalars().first()
        if product is None:
            return {"error": "No product found with that id."}
    else:
        needle = (sku or product_name or "").strip()
        if not needle:
            return {"error": "Need a product id, name, or SKU to look up."}
        matches = (
            await db.execute(
                select(Product).where(
                    Product.tenant_id == tenant_id,
                    or_(Product.name.ilike(f"%{needle}%"), Product.sku.ilike(f"%{needle}%")),
                )
            )
        ).scalars().all()
        if not matches:
            return {"error": f'No product matching "{needle}".'}
        if len(matches) > 1:
            return {
                "error": "ambiguous",
                "matches": [{"id": str(p.id), "name": p.name, "sku": p.sku} for p in matches[:10]],
            }
        product = matches[0]

    category_name = None
    if product.category_id:
        category = (
            await db.execute(select(Category).where(Category.id == product.category_id))
        ).scalars().first()
        category_name = category.name if category else None

    attrs = product.attributes or {}
    return {
        "id": str(product.id),
        "name": product.name,
        "sku": product.sku,
        "category_name": category_name,
        "price": _money(product.price_cents, product.currency),
        "compare_at": _money(product.compare_at_cents, product.currency) if product.compare_at_cents else None,
        "stock": product.stock,
        "track_stock": product.track_stock,
        "is_active": product.is_active,
        "unit": product.unit,
        "free_delivery": product.free_delivery,
        "delivery_charge": _money(product.delivery_charge_cents, product.currency) if product.delivery_charge_cents else None,
        "short_description": product.short_description,
        "description": product.description,
        "features": product.features or [],
        "variants": attrs.get("variants") or [],
        "photo_count": len(product.images or []),
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


async def _list_categories(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Name + active product count per category — same shape convention as
    _list_products (id included so a follow-up add_category/set_categories
    proposal can reference what's real instead of the model guessing).
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    rows = (
        await db.execute(
            select(Category.id, Category.name, func.count(Product.id))
            .outerjoin(
                Product,
                (Product.category_id == Category.id) & (Product.is_active.is_(True)),
            )
            .where(Category.site_id == site.id)
            .group_by(Category.id, Category.name, Category.sort_order)
            .order_by(Category.sort_order)
        )
    ).all()
    return {
        "categories": [
            {"id": str(cid), "name": name, "product_count": int(count)}
            for cid, name, count in rows
        ]
    }


async def _get_fraud_status(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Live read of Settings -> Fraud Protection: which checkout-time rules
    (see app/models.py's Site.fraud_rules, shape mirrored from
    dashboard/components/fraud/fraud-data.ts's FRAUD_RULES) are actually on,
    plus real blocklist sizes. Previously the system prompt only had static
    text describing the feature conceptually — this is the first live lookup
    of a merchant's OWN configured rules.
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    rules = site.fraud_rules or {}

    def rule(rule_id: str) -> dict:
        r = rules.get(rule_id) or {}
        return {"enabled": bool(r.get("enabled")), "threshold": r.get("value")}

    phone_count = (
        await db.execute(
            select(func.count()).select_from(FraudBlocklistEntry).where(
                FraudBlocklistEntry.site_id == site.id
            )
        )
    ).scalar_one()
    ip_count = (
        await db.execute(
            select(func.count()).select_from(FraudIpBlocklistEntry).where(
                FraudIpBlocklistEntry.site_id == site.id
            )
        )
    ).scalar_one()

    return {
        "hold_first_high_value": rule("hold_first_high_value"),
        "flag_burst_orders": rule("flag_burst_orders"),
        "block_blocklist": rule("block_blocklist"),
        "device_pending_lock": rule("device_pending_lock"),
        "device_cooldown": rule("device_cooldown"),
        "phone_blocklist_count": phone_count,
        "ip_blocklist_count": ip_count,
    }


async def _list_events(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Name + discount + status per event — same shape convention as
    _list_categories (this is the read side create_event's confirm card
    depends on the model having called first)."""
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    rows = (
        await db.execute(
            select(Event).where(Event.site_id == site.id).order_by(Event.created_at.desc())
        )
    ).scalars().all()
    # Event.products is lazy="selectin" (see app/models.py) — len() here is
    # free, not an extra query per row.
    return {
        "events": [
            {
                "id": str(e.id),
                "name": e.name,
                "discount_percent": e.discount_percent,
                "is_active": e.is_active,
                "is_popup": e.is_popup,
                "image_only": e.image_only,
                "product_count": len(e.products),
            }
            for e in rows
        ]
    }


async def _search_customers(
    db: AsyncSession, tenant_id: uuid.UUID, query: str | None = None, limit: int = 10
) -> dict:
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    limit = min(max(limit or 10, 1), 25)
    stmt = select(Customer).where(Customer.site_id == site.id)
    if query:
        q = f"%{query.strip()}%"
        stmt = stmt.where(or_(Customer.phone.ilike(q), Customer.name.ilike(q)))
    stmt = stmt.order_by(Customer.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "customers": [
            {"id": str(c.id), "phone": c.phone, "name": c.name, "email": c.email}
            for c in rows
        ]
    }


async def _get_customer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str | None = None,
    customer_id: str | None = None,
) -> dict:
    """Same order_count/total_spent_cents/last_order_at/risk_score
    computation as app/api/customers.py's get_customer — reusing
    risk_score.compute_risk_score rather than a second implementation, same
    "one real source of truth" reasoning as everything else in this module.
    Only linked orders count (Order.customer_id), same caveat as that route:
    orders placed before customer-linking shipped aren't included.
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    customer = None
    if customer_id:
        try:
            cid = uuid.UUID(customer_id)
        except ValueError:
            return {"error": "Invalid customer id."}
        customer = (
            await db.execute(
                select(Customer).where(Customer.id == cid, Customer.site_id == site.id)
            )
        ).scalars().first()
    elif phone:
        customer = (
            await db.execute(
                select(Customer).where(
                    Customer.site_id == site.id, Customer.phone == phone.strip()
                )
            )
        ).scalars().first()
    else:
        return {"error": "Need a phone number or customer id to look up."}

    if customer is None:
        return {"error": "No customer found."}

    orders, order_count = await crud.list_scoped(
        db, Order, tenant_id,
        filters=[Order.customer_id == customer.id],
        order_by=Order.created_at.desc(), limit=100, offset=0,
    )
    total_spent = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.customer_id == customer.id, Order.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    last_order_at = orders[0].created_at if orders else None

    latest_ip = next((o.ip_address for o in orders if o.ip_address), None)
    ip_blocklisted = False
    if latest_ip:
        ip_blocklisted = (
            await db.execute(
                select(FraudIpBlocklistEntry.id).where(
                    FraudIpBlocklistEntry.site_id == site.id,
                    FraudIpBlocklistEntry.ip_address == latest_ip,
                ).limit(1)
            )
        ).scalar_one_or_none() is not None
    open_order_count = sum(1 for o in orders if o.status in ("pending", "paid"))
    latest_device_id = next((o.device_id for o in orders if o.device_id), None)

    risk = risk_score_module.compute_risk_score(
        orders=orders,
        current_device_id=latest_device_id,
        ip_blocklisted=ip_blocklisted,
        has_open_duplicate=open_order_count > 1,
    )
    currency = await _tenant_currency(db, tenant_id)

    return {
        "id": str(customer.id),
        "phone": customer.phone,
        "name": customer.name,
        "email": customer.email,
        "order_count": order_count,
        "total_spent": _money(total_spent, currency),
        "last_order_at": last_order_at.isoformat() if last_order_at else None,
        "risk_score": risk["score"],
        "risk_label": risk["label"],
    }


async def _list_courier_connections(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """provider/status/label/last_verified_at only — deliberately never
    api_key_hint or webhook_secret/webhook_url, even though the real
    dashboard API returns those to an authenticated session. A chat tool
    result can end up quoted back in plain text, and there's no reason it
    should ever handle those fields (see this tool's TOOL_DECLARATIONS
    description for the same point)."""
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        return {"error": "No site found for this account"}

    rows = (
        await db.execute(
            select(CourierConnection).where(CourierConnection.site_id == site.id)
        )
    ).scalars().all()
    return {
        "connections": [
            {
                "provider": c.provider,
                "status": c.status,
                "label": c.label,
                "last_verified_at": c.last_verified_at.isoformat() if c.last_verified_at else None,
            }
            for c in rows
        ]
    }


_HANDLERS = {
    "get_business_overview": _get_business_overview,
    "list_products": _list_products,
    "get_product": _get_product,
    "list_orders": _list_orders,
    "get_order": _get_order,
    "get_sales_summary": _get_sales_summary,
    "get_site_info": _get_site_info,
    "get_media_stats": _get_media_stats,
    "get_billing_status": _get_billing_status,
    "list_categories": _list_categories,
    "get_fraud_status": _get_fraud_status,
    "list_events": _list_events,
    "search_customers": _search_customers,
    "get_customer": _get_customer,
    "list_courier_connections": _list_courier_connections,
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
