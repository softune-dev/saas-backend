"""AI agent tools for Site Settings: contact, SEO, delivery, About, FAQs and
the legal pages.

Why this exists: real conversations ended in "I cannot add shipping data" even
though the dashboard could do it, because the agent could read these settings
but had no way to write them.

Every tool writes the same JSONB shape the settings pages save (see
dashboard/lib/api/site-settings.ts), so the pages open the result normally.
Each write records an Undo. Values are checked here rather than by a form:
the model is not a trusted caller, and tracking ids in particular are pasted
straight into a <script> on the storefront (templates/*/app/layout.tsx), so a
loose value would be script injection on a live store.

Tenant isolation: tenant_id comes from the verified JWT via the agent loop,
never from tool arguments, as in app/ai_agent_tools.py.
"""

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_actions, ai_agent_media, cache, queue
from app.ai_agent_tools import _cents, _clip, _forget_tool_cache, _record, _site
from app.models import Product, Site

KIND = "settings"
MAX_LOCATIONS = 10  # ProductUpdate.delivery_charges allows 10 per product
MAX_FAQS = 40
MAX_LEGAL_CHARS = 30_000

_URL = re.compile(r"^https?://[^\s<>\"']{3,500}$")
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_PHONE = re.compile(r"^[+0-9][0-9 ()-]{5,24}$")
_TIME = re.compile(r"^[0-9]{1,2}:[0-9]{2}( ?[AP]M)?$")
SOCIALS = ("facebook", "instagram", "tiktok", "youtube", "x", "linkedin", "whatsapp", "telegram", "other")

SEO_TEXT: dict[str, int] = {
    "title_suffix": 70, "meta_description": 300, "keywords": 300, "og_title": 120, "og_description": 300,
}
# Rendered inside inline scripts: only the exact id format may pass.
TRACKING: dict[str, re.Pattern] = {
    "google_analytics": re.compile(r"^(G|UA|AW)-[A-Z0-9-]{4,24}$"),
    "gtm_container_id": re.compile(r"^GTM-[A-Z0-9]{4,12}$"),
    "facebook_pixel": re.compile(r"^[0-9]{8,20}$"),
    "tiktok_pixel": re.compile(r"^[A-Za-z0-9]{10,30}$"),
    "google_search_console": re.compile(r"^[A-Za-z0-9_-]{10,100}$"),
}

_STR = {"type": "STRING"}

DECLARATIONS: list[dict] = [
    {
        "name": "update_contact_info",
        "description": "Change the store's public contact details: name, description, phone, WhatsApp, email, address, map link, opening hours, social links, support note. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "business_name": _STR, "description": _STR, "phone": _STR, "whatsapp": _STR, "email": _STR,
            "street": _STR, "city": _STR, "region": _STR, "postal_code": _STR, "country": _STR,
            "map_url": _STR, "support_note": _STR,
            "socials": {"type": "ARRAY", "description": "Empty url removes that link.", "items": {
                "type": "OBJECT",
                "properties": {"platform": {"type": "STRING", "enum": list(SOCIALS)}, "url": _STR},
                "required": ["platform", "url"],
            }},
            "hours": {"type": "ARRAY", "description": "Full week; replaces existing hours.", "items": {
                "type": "OBJECT",
                "properties": {"day": _STR, "open": _STR, "close": _STR, "closed": {"type": "BOOLEAN"}},
                "required": ["day"],
            }},
        }},
    },
    {
        "name": "update_seo",
        "description": "Change SEO and tracking: title suffix, meta description, keywords, social share text and image, favicon, Google visibility, sitemap, Analytics/GTM/Search Console/Facebook/TikTok ids. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            **{name: _STR for name in SEO_TEXT},
            **{name: _STR for name in TRACKING},
            "og_image_url": {"type": "STRING", "description": "Hosted URL from an attached image."},
            "favicon_url": {"type": "STRING", "description": "Hosted URL from an attached image."},
            "hide_from_google": {"type": "BOOLEAN"},
            "sitemap_enabled": {"type": "BOOLEAN"},
        }},
    },
    {
        "name": "set_delivery_locations",
        "description": "Replace the store's delivery locations and charges, and apply them to products so checkout really charges them. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "locations": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"name": _STR, "charge_taka": {"type": "NUMBER"}},
                "required": ["name", "charge_taka"],
            }},
            "apply_to_products": {"type": "BOOLEAN", "description": "Default true. False only if asked."},
        }, "required": ["locations"]},
    },
    {
        "name": "set_product_delivery",
        "description": "Make one product free delivery, or give it the store's delivery charges. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "product_name": _STR, "product_id": _STR, "free_delivery": {"type": "BOOLEAN"},
        }, "required": ["free_delivery"]},
    },
    {
        "name": "update_about_page",
        "description": "Write the About Us page: heading, story paragraphs, image. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "heading": _STR,
            "paragraphs": {"type": "ARRAY", "items": _STR},
            "image_url": {"type": "STRING", "description": "Hosted URL from an attached image."},
        }},
    },
    {
        "name": "set_faqs",
        "description": "Replace the store's whole FAQ list. Send every FAQ to keep, plus new ones. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "faqs": {"type": "ARRAY", "items": {
                "type": "OBJECT", "properties": {"question": _STR, "answer": _STR},
                "required": ["question", "answer"],
            }},
        }, "required": ["faqs"]},
    },
    {
        "name": "update_legal_page",
        "description": "Write, publish or unpublish the Privacy Policy or Terms page. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "page": {"type": "STRING", "enum": ["privacy", "terms"]},
            "title": _STR, "content": _STR, "published": {"type": "BOOLEAN"},
        }, "required": ["page"]},
    },
]


async def apply_site_write(db: AsyncSession, site: Site) -> None:
    await db.commit()
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    if site.status == "published":
        await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})


def _result(change: Any, **extra: Any) -> dict:
    return {"ok": True, **extra, "change": {"id": str(change.id), "summary": change.summary, "kind": KIND}}


async def patch_column(
    db: AsyncSession, tenant_id: uuid.UUID, tool: str, column: str, patch: dict, summary: str,
    products: list[dict] | None = None, **extra: Any,
) -> dict:
    """Merge `patch` into one dict column of the site. Stores only the keys it
    touches, so Undo can leave a later edit to any other key alone."""
    site = await _site(db, tenant_id)
    current = getattr(site, column) or {}
    before = {k: current.get(k) for k in patch}
    if before == patch and not products:
        return {"ok": True, "unchanged": True, "message": "Already set to that, nothing to change."}
    setattr(site, column, {**current, **patch})  # new object, so the JSONB is seen as dirty
    await apply_site_write(db, site)
    undo = {"type": "site_json", "column": column, "before": before, "after": patch, "products": products or []}
    change = await _record(db, site, tool, summary, undo)
    await _forget_tool_cache(tenant_id)
    return _result(change, **extra)


async def undo_site_json(db: AsyncSession, site: Site, spec: dict) -> int:
    """Puts back what the change overwrote and returns how many values it left
    alone. A value edited by hand since then is newer, so it stays."""
    column, before, after = spec["column"], spec.get("before") or {}, spec.get("after") or {}
    current = getattr(site, column)
    skipped = 0
    if column == "faqs":
        if current == after.get("items"):
            site.faqs = before.get("items") or []
        else:
            skipped = 1
    else:
        restore = {k: v for k, v in before.items() if (current or {}).get(k) == after.get(k)}
        skipped = len(before) - len(restore)
        setattr(site, column, {**(current or {}), **restore})
    snapshots = {s["id"]: s for s in spec.get("products") or []}
    if snapshots:
        rows = (await db.execute(select(Product).where(
            Product.tenant_id == site.tenant_id, Product.id.in_([uuid.UUID(i) for i in snapshots])
        ))).scalars().all()
        for row in rows:
            row.free_delivery = snapshots[str(row.id)]["free_delivery"]
            row.delivery_charges = snapshots[str(row.id)]["delivery_charges"]
    await apply_site_write(db, site)
    return skipped


# --- contact -----------------------------------------------------------------

def _hours(rows: list) -> tuple[list[dict], list[str]] | str:
    hours: list[dict] = []
    for row in rows[:7]:
        day = _clip(row.get("day"), 20)
        closed = bool(row.get("closed"))
        opens, closes = _clip(row.get("open"), 8), _clip(row.get("close"), 8)
        if not day or (not closed and not (_TIME.match(opens) and _TIME.match(closes))):
            return "Each day needs a name and open/close times like 10:00 AM, or closed=true."
        hours.append({"day": day, "open": opens, "close": closes, "closed": closed})
    # Same strings the contact page computes on save (schema.org openingHours).
    return hours, [f"{h['day'][:2]} {h['open']}-{h['close']}" for h in hours if not h["closed"]]


async def update_contact_info(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    current = site.business or {}
    patch: dict[str, Any] = {}
    for arg, key, limit in (("business_name", "name", 120), ("description", "description", 300),
                            ("support_note", "support_note", 300)):
        if args.get(arg) is not None:
            patch[key] = _clip(args[arg], limit)
    for key in ("phone", "whatsapp"):
        if args.get(key) is not None:
            value = _clip(args[key], 25)
            if value and not _PHONE.match(value):
                return {"error": f"{key} doesn't look like a phone number."}
            patch[key] = value
    if args.get("email") is not None:
        value = _clip(args["email"], 200)
        if value and not _EMAIL.match(value):
            return {"error": "That email address isn't valid."}
        patch["email"] = value
    if args.get("map_url") is not None:
        value = _clip(args["map_url"], 500)
        if value and not _URL.match(value):
            return {"error": "map_url must be a full https:// link."}
        patch["map_url"] = value
    address = {k: _clip(args[k], 200) for k in ("street", "city", "region", "postal_code", "country") if args.get(k) is not None}
    if address:
        patch["address"] = {**(current.get("address") or {}), **address}
    if args.get("socials") is not None:
        socials = dict(current.get("socials") or {})
        for item in args["socials"]:
            platform, url = item.get("platform"), _clip(item.get("url"), 500)
            if platform not in SOCIALS:
                return {"error": "Unknown social platform.", "choices": list(SOCIALS)}
            if url and not _URL.match(url):
                return {"error": f"The {platform} link must be a full https:// address."}
            if url:
                socials[platform] = url
            else:
                socials.pop(platform, None)
        patch["socials"] = socials
    if args.get("hours") is not None:
        parsed = _hours(args["hours"])
        if isinstance(parsed, str):
            return {"error": parsed}
        patch["hours"], patch["opening_hours"] = parsed
    if not patch:
        return {"error": "Nothing to change. Pass at least one contact field."}
    return await patch_column(db, tenant_id, "update_contact_info", "business", patch, "Updated contact details")


# --- SEO ---------------------------------------------------------------------

async def update_seo(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    patch: dict[str, Any] = {}
    for key, limit in SEO_TEXT.items():
        if args.get(key) is not None:
            patch[key] = _clip(args[key], limit)
    for key, pattern in TRACKING.items():
        if args.get(key) is not None:
            value = str(args[key]).strip()
            if value and not pattern.match(value):
                return {"error": f"{key} isn't a valid id. Ask the merchant to copy just the id itself."}
            patch[key] = value
    for arg, key in (("og_image_url", "og_image"), ("favicon_url", "favicon")):
        if args.get(arg):
            patch[key] = ai_agent_media.own_image_url(site, args[arg])
    if args.get("hide_from_google") is not None:
        patch["noindex"] = bool(args["hide_from_google"])
    if args.get("sitemap_enabled") is not None:
        patch["sitemap_enabled"] = bool(args["sitemap_enabled"])
    if not patch:
        return {"error": "Nothing to change. Pass at least one SEO field."}
    return await patch_column(db, tenant_id, "update_seo", "seo", patch, "Updated SEO settings")


# --- delivery ----------------------------------------------------------------

def store_delivery_charges(site: Site) -> list[dict]:
    """The site's locations in the shape a product stores: a priced copy, not a
    live link (see ProductDeliveryCharge)."""
    return [
        {"name": loc["name"], "charge_cents": int(loc.get("charge_cents") or 0)}
        for loc in (site.shipping or {}).get("locations", []) if loc.get("name")
    ][:MAX_LOCATIONS]


async def set_delivery_locations(
    db: AsyncSession, tenant_id: uuid.UUID, locations: list, apply_to_products: bool = True
) -> dict:
    """Settings -> Shipping is only a list to pick from: checkout charges what
    each PRODUCT stores, and products default to free delivery. Writing the
    list alone would report success while customers still paid nothing, so the
    charges are copied onto products too, with each one's old values kept for
    Undo."""
    if len(locations) > MAX_LOCATIONS:
        return {"error": f"A store can have up to {MAX_LOCATIONS} delivery locations."}
    site = await _site(db, tenant_id)
    cleaned: list[dict] = []
    for index, loc in enumerate(locations):
        name = _clip(loc.get("name"), 60)
        if not name:
            return {"error": "Every delivery location needs a name."}
        cleaned.append({"id": f"ai-{index}-{uuid.uuid4().hex[:8]}", "name": name, "charge_cents": _cents(loc.get("charge_taka")) or 0})
    if len({c["name"] for c in cleaned}) != len(cleaned):
        return {"error": "Two delivery locations have the same name."}

    snapshots: list[dict] = []
    if apply_to_products:
        charges = [{"name": c["name"], "charge_cents": c["charge_cents"]} for c in cleaned]
        rows = (await db.execute(select(Product).where(
            Product.tenant_id == site.tenant_id, Product.site_id == site.id
        ))).scalars().all()
        for row in rows:
            snapshots.append({"id": str(row.id), "free_delivery": row.free_delivery, "delivery_charges": row.delivery_charges or []})
            row.free_delivery = not charges
            row.delivery_charges = charges
    note = (
        f"Applied to {len(snapshots)} products." if apply_to_products
        else "Products were not changed, so checkout charges stay as they were."
    )
    return await patch_column(
        db, tenant_id, "set_delivery_locations", "shipping", {"locations": cleaned},
        f"Set {len(cleaned)} delivery locations", products=snapshots, note=note,
    )


async def set_product_delivery(
    db: AsyncSession, tenant_id: uuid.UUID, free_delivery: bool,
    product_name: str | None = None, product_id: str | None = None,
) -> dict:
    site = await _site(db, tenant_id)
    row = await ai_actions._find_product(db, site, product_id, product_name)
    charges = [] if free_delivery else store_delivery_charges(site)
    if not free_delivery and not charges:
        return {"error": "This store has no delivery locations yet. Set them first."}
    snapshot = {"id": str(row.id), "free_delivery": row.free_delivery, "delivery_charges": row.delivery_charges or []}
    row.free_delivery = bool(free_delivery)
    row.delivery_charges = charges
    summary = f"{'Free delivery on' if free_delivery else 'Delivery charges on'} “{row.name}”"
    await apply_site_write(db, site)
    change = await _record(db, site, "set_product_delivery", summary, {
        "type": "site_json", "column": "shipping", "before": {}, "after": {}, "products": [snapshot],
    })
    await _forget_tool_cache(tenant_id)
    return _result(change)


# --- pages -------------------------------------------------------------------

async def update_about_page(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    patch: dict[str, Any] = {}
    if args.get("heading") is not None:
        patch["heading"] = _clip(args["heading"], 120)
    if args.get("paragraphs") is not None:
        patch["paragraphs"] = [_clip(p, 2000) for p in args["paragraphs"] if str(p).strip()][:12]
    if args.get("image_url"):
        patch["image"] = ai_agent_media.own_image_url(site, args["image_url"])
    if not patch:
        return {"error": "Nothing to change on the About page."}
    return await patch_column(db, tenant_id, "update_about_page", "about", patch, "Updated the About page")


async def set_faqs(db: AsyncSession, tenant_id: uuid.UUID, faqs: list) -> dict:
    site = await _site(db, tenant_id)
    before = list(site.faqs or [])
    items = [
        {"id": uuid.uuid4().hex[:12], "question": _clip(f.get("question"), 300), "answer": _clip(f.get("answer"), 2000)}
        for f in faqs[:MAX_FAQS] if str(f.get("question") or "").strip() and str(f.get("answer") or "").strip()
    ]
    if not items and faqs:
        return {"error": "Each FAQ needs a question and an answer."}
    site.faqs = items
    await apply_site_write(db, site)
    change = await _record(db, site, "set_faqs", f"Updated the FAQs ({len(items)})", {
        "type": "site_json", "column": "faqs", "before": {"items": before}, "after": {"items": items},
    })
    await _forget_tool_cache(tenant_id)
    return _result(change)


async def update_legal_page(
    db: AsyncSession, tenant_id: uuid.UUID, page: str, title: str | None = None,
    content: str | None = None, published: bool | None = None,
) -> dict:
    if page not in ("privacy", "terms"):
        return {"error": "page must be privacy or terms"}
    site = await _site(db, tenant_id)
    doc = dict((site.legal or {}).get(page) or {})
    label = "Privacy Policy" if page == "privacy" else "Terms"
    doc.setdefault("title", label)
    doc.setdefault("content", "")
    doc.setdefault("published", False)
    if title is not None:
        doc["title"] = _clip(title, 120) or doc["title"]
    if content is not None:
        doc["content"] = str(content).strip()[:MAX_LEGAL_CHARS]
    if published is not None:
        doc["published"] = bool(published)
    if doc["published"] and not doc["content"]:
        return {"error": f"The {label} page has no text yet, so it can't be published."}
    return await patch_column(db, tenant_id, "update_legal_page", "legal", {page: doc}, f"Updated the {label} page")


HANDLERS = {
    "update_contact_info": update_contact_info,
    "update_seo": update_seo,
    "set_delivery_locations": set_delivery_locations,
    "set_product_delivery": set_product_delivery,
    "update_about_page": update_about_page,
    "set_faqs": set_faqs,
    "update_legal_page": update_legal_page,
}
