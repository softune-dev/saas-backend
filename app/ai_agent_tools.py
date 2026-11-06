"""Tools that let the AI assistant ACT on a store, plus the Undo that makes
acting safe.

Why this exists: the chat assistant used to be read-only plus a handful of
"proposed" writes the merchant had to confirm in a form. Real conversations
showed what merchants actually ask for — "change my colors to black and
gold", "put this in my why-choose-us section", "আপনি করে দেন" (you do it) —
and the assistant could only answer with instructions. These tools do the
work directly.

Two kinds of write:
  * APPLIED — design, homepage text, new categories/products/events and
    product edits happen immediately. Before each one, what it overwrites is
    stored as an AiAgentChange row, and POST /ai/changes/{id}/undo puts it
    back. Cheap to reverse, so no confirm step.
  * PROPOSED — replacing ALL categories and changing an order's status are
    hard to reverse or affect a customer, so they still go through the
    existing confirm card (app/ai_actions.py + the /ai/actions/* endpoints).

Tenant isolation: tenant_id always comes from the verified JWT via the agent
loop (app/ai_agent.py), never from the model's arguments, exactly like
app/ai_tools.py. Every write resolves the tenant's own site first.
"""

import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app import ai_actions, cache, crud, queue
from app.config import settings
from app.models import AiAgentChange, Site, Template

# --- what the agent may change in a theme --------------------------------

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# Must match the families the storefronts actually load (see
# dashboard/components/themes/editor/editor-types.ts displayFontOptions /
# bodyFontOptions) — anything else silently falls back on the storefront.
DISPLAY_FONTS = (
    "fraunces", "playfair", "cormorant", "libre-baskerville", "dm-serif-display", "spectral",
    "bodoni-moda", "newsreader", "instrument-serif", "prata", "archivo-black",
    "big-shoulders-display",
)
BODY_FONTS = (
    "inter", "manrope", "work-sans", "outfit", "karla", "sora", "plus-jakarta-sans",
    "space-grotesk", "urbanist", "figtree", "dm-sans", "nunito-sans",
)
BUTTON_STYLES = ("Pill", "Rounded", "Square")

# Homepage text: snake_case tool argument -> theme key, with a length cap.
# Only keys SiteEditorSettings defines, so the editor opens the result like
# any hand-made theme.
TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "site_name": ("siteName", 60),
    "tagline": ("tagline", 120),
    "categories_title": ("categoriesTitle", 60),
    "featured_products_title": ("featureProductsTitle", 60),
    "category_showcase_title": ("categoryShowcaseTitle", 60),
    "why_title": ("whyTitle", 60),
    "why_1_title": ("why1Title", 60), "why_1_text": ("why1", 240),
    "why_2_title": ("why2Title", 60), "why_2_text": ("why2", 240),
    "why_3_title": ("why3Title", 60), "why_3_text": ("why3", 240),
    "features_title": ("featuresTitle", 60),
    "feature_1_title": ("feature1Title", 60), "feature_1_text": ("feature1", 240),
    "feature_2_title": ("feature2Title", 60), "feature_2_text": ("feature2", 240),
    "feature_3_title": ("feature3Title", 60), "feature_3_text": ("feature3", 240),
    "testimonials_title": ("testimonialsTitle", 60),
    "banner_title": ("ctaTitle", 80),
    "banner_body": ("ctaBody", 240),
    "banner_button": ("ctaButton", 30),
    "footer_description": ("footerDescription", 300),
}
_DESIGN_KEYS = ("primaryColor", "accentColor", "surfaceColor", "displayFont", "bodyFont", "buttonStyle")

# One {field, value} list instead of 24 named properties: same capability,
# about half the tokens on every model call.
_text_props = {
    "changes": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "field": {"type": "STRING", "enum": list(TEXT_FIELDS)},
                "value": {"type": "STRING"},
            },
            "required": ["field", "value"],
        },
    },
    "announcement_items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "1-5 short top-bar messages."},
}

DECLARATIONS: list[dict] = [
    {
        "name": "get_storefront_design",
        "description": (
            "Current colors, fonts, button style and ALL homepage text (hero tagline, "
            "announcement bar, section titles, why-choose-us points, features, banner/CTA, "
            "footer) plus the live store URL. Call this before changing design or text."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "update_storefront_design",
        "description": (
            "Change the store's colors, fonts or button shape. Applied to the live store "
            "immediately; the merchant can undo. Colors are #RRGGBB."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "primary_color": {"type": "STRING", "description": "Main brand color: buttons, highlights."},
                "accent_color": {"type": "STRING", "description": "Secondary color: badges, accents."},
                "surface_color": {"type": "STRING", "description": "Page/card background color."},
                # Not an enum: listing all 24 fonts here cost ~200 tokens on
                # every call. The valid keys come back from
                # get_storefront_design (font_choices) and are checked below.
                "display_font": {"type": "STRING", "description": "A key from font_choices.display"},
                "body_font": {"type": "STRING", "description": "A key from font_choices.body"},
                "button_style": {"type": "STRING", "enum": list(BUTTON_STYLES)},
            },
        },
    },
    {
        "name": "update_homepage_text",
        "description": (
            "Write or replace text on the homepage: tagline, announcement bar, section "
            "titles, the 3 why-choose-us points, the 3 features, the banner/CTA "
            "(title, body, button) and the footer description. Only send fields you are "
            "changing. Applied immediately; the merchant can undo."
        ),
        "parameters": {"type": "OBJECT", "properties": _text_props},
    },
    {
        "name": "add_category",
        "description": "Create one new category (keeps all existing ones). Applied immediately; undo deletes it.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"name": {"type": "STRING"}, "description": {"type": "STRING"}},
            "required": ["name"],
        },
    },
    {
        "name": "create_discount_event",
        "description": (
            "Create a sale/discount event (also used for the storefront popup). "
            "Applied immediately; undo deletes it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING"},
                "discount_percent": {"type": "INTEGER", "description": "1-90"},
                "description": {"type": "STRING"},
                "cta_label": {"type": "STRING"},
                "is_active": {"type": "BOOLEAN"},
                "is_popup": {"type": "BOOLEAN", "description": "Show as the storefront popup (replaces the current one)."},
            },
            "required": ["name", "discount_percent"],
        },
    },
    {
        "name": "propose_replace_all_categories",
        "description": (
            "Replace the store's ENTIRE category list. Destructive, so this only shows the "
            "merchant a confirm card; nothing changes until they confirm."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"categories": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["categories"],
        },
    },
    {
        "name": "propose_order_status",
        "description": (
            "Change an order's status (pending, paid, fulfilled, cancelled, refunded). Affects a "
            "customer, so this only shows a confirm card."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "order_number": {"type": "STRING"},
                "status": {"type": "STRING", "enum": ["pending", "paid", "fulfilled", "cancelled", "refunded"]},
                "notes": {"type": "STRING"},
            },
            "required": ["order_number", "status"],
        },
    },
]

WRITE_TOOLS = {d["name"] for d in DECLARATIONS} - {"get_storefront_design"}
PROPOSAL_TOOLS = {"propose_replace_all_categories", "propose_order_status"}


# --- helpers ---------------------------------------------------------------

async def _site(db: AsyncSession, tenant_id: uuid.UUID) -> Site:
    return await ai_actions._resolve_site(db, tenant_id)


async def _template_key(db: AsyncSession, site: Site) -> str | None:
    return (await db.execute(select(Template.key).where(Template.id == site.template_id))).scalar_one_or_none()


def _store_url(site: Site) -> str:
    return f"https://{site.custom_domain or f'{site.subdomain}.{settings.site_base_domain}'}"


async def _forget_tool_cache(tenant_id: uuid.UUID) -> None:
    # app/ai_tools.py caches read results for 60s; after a write the agent's
    # next read must see the new data, not the cached old one.
    await cache.drop_prefix(f"ai:tool:{tenant_id}:")


async def _record(
    db: AsyncSession, site: Site, tool: str, summary: str, undo: dict
) -> AiAgentChange:
    change = AiAgentChange(
        tenant_id=site.tenant_id, site_id=site.id, tool=tool, summary=summary, undo=undo
    )
    db.add(change)
    await db.commit()
    await db.refresh(change)
    return change


async def _write_theme(db: AsyncSession, site: Site, patch: dict) -> None:
    site.theme = {**(site.theme or {}), **patch}
    flag_modified(site, "theme")
    await db.commit()
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    if site.status == "published":
        await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})


async def _theme_change(
    db: AsyncSession, tenant_id: uuid.UUID, tool: str, patch: dict, summary: str
) -> dict:
    site = await _site(db, tenant_id)
    theme = site.theme or {}
    before = {k: theme.get(k) for k in patch}
    if all(before[k] == v for k, v in patch.items()):
        return {"ok": True, "unchanged": True, "message": "Already set to that — nothing to change."}
    await _write_theme(db, site, patch)
    change = await _record(db, site, tool, summary, {"type": "theme", "before": before, "after": patch})
    await _forget_tool_cache(tenant_id)
    return {
        "ok": True,
        "change": {
            "id": str(change.id), "summary": summary, "kind": "theme",
            "theme": site.theme, "template_key": await _template_key(db, site),
            "store_url": _store_url(site),
        },
    }


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]

def _cents(taka: Any) -> int | None:
    if taka is None:
        return None
    try:
        return max(0, int(round(float(taka) * 100)))  # money as integer cents (rule 7)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Price must be a number")


# --- tools ----------------------------------------------------------------

async def get_storefront_design(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    site = await _site(db, tenant_id)
    theme = site.theme or {}
    text = {name: theme.get(key) or "" for name, (key, _) in TEXT_FIELDS.items()}
    text["announcement_items"] = theme.get("announcementItems") or []
    return {
        "store_url": _store_url(site),
        "published": site.status == "published",
        "design": {k: theme.get(k) for k in _DESIGN_KEYS},
        "homepage_text": text,
        "hero_has_image": bool(theme.get("heroImages")),
        "why_section_has_image": bool(theme.get("whyImage")),
        "font_choices": {"display": list(DISPLAY_FONTS), "body": list(BODY_FONTS)},
    }


async def update_storefront_design(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    patch: dict[str, str] = {}
    for arg, key in (("primary_color", "primaryColor"), ("accent_color", "accentColor"), ("surface_color", "surfaceColor")):
        if args.get(arg):
            value = str(args[arg]).strip()
            if not _HEX.match(value):
                return {"error": f"{arg} must be a #RRGGBB hex color, got {value!r}"}
            patch[key] = value.upper()
    if args.get("display_font"):
        if args["display_font"] not in DISPLAY_FONTS:
            return {"error": "Unknown display font", "choices": list(DISPLAY_FONTS)}
        patch["displayFont"] = args["display_font"]
    if args.get("body_font"):
        if args["body_font"] not in BODY_FONTS:
            return {"error": "Unknown body font", "choices": list(BODY_FONTS)}
        patch["bodyFont"] = args["body_font"]
    if args.get("button_style"):
        if args["button_style"] not in BUTTON_STYLES:
            return {"error": "button_style must be Pill, Rounded or Square"}
        patch["buttonStyle"] = args["button_style"]
    if not patch:
        return {"error": "Nothing to change — pass at least one color, font or button style."}
    return await _theme_change(db, tenant_id, "update_storefront_design", patch, "Updated store design")


async def update_homepage_text(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    patch: dict[str, Any] = {}
    # {field, value} pairs (the tool schema); plain keyword fields are also
    # accepted so internal callers and tests can pass tagline="...".
    pairs = [(c.get("field"), c.get("value")) for c in (args.get("changes") or []) if isinstance(c, dict)]
    pairs += [(name, args[name]) for name in TEXT_FIELDS if args.get(name) is not None]
    for name, value in pairs:
        if name in TEXT_FIELDS and value is not None:
            key, limit = TEXT_FIELDS[name]
            patch[key] = _clip(value, limit)
    if args.get("announcement_items"):
        items = [_clip(i, 80) for i in args["announcement_items"] if str(i).strip()][:5]
        if items:
            patch["announcementItems"] = items
    if not patch:
        return {"error": "Nothing to change — pass at least one text field."}
    return await _theme_change(db, tenant_id, "update_homepage_text", patch, "Updated homepage text")


async def add_category(db: AsyncSession, tenant_id: uuid.UUID, name: str, description: str | None = None) -> dict:
    site = await _site(db, tenant_id)
    created = await ai_actions.add_category(db, tenant_id, name, description)
    change = await _record(
        db, site, "add_category", f"Added category “{created['name']}”",
        {"type": "delete_category", "category_id": created["id"]},
    )
    await _forget_tool_cache(tenant_id)
    return {"ok": True, "category": created, "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}


async def create_discount_event(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    created = await ai_actions.create_event(
        db, tenant_id, name=args.get("name", ""), description=args.get("description"),
        discount_percent=args.get("discount_percent"), cta_label=args.get("cta_label") or "Shop now",
        is_active=bool(args.get("is_active")), is_popup=bool(args.get("is_popup")),
    )
    change = await _record(
        db, site, "create_discount_event", f"Created event “{created['name']}”",
        {"type": "delete_event", "event_id": created["id"]},
    )
    await _forget_tool_cache(tenant_id)
    return {"ok": True, "event": created, "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}


def proposal_for(name: str, args: dict) -> dict:
    """PROPOSAL_TOOLS don't write — they return the pending_action the
    existing confirm cards already understand (app/ai_forms.py)."""
    if name == "propose_replace_all_categories":
        cats = [str(c).strip() for c in (args.get("categories") or []) if str(c).strip()][:40]
        return {"type": "set_categories", "categories": cats}
    return {
        "type": "update_order_status", "order_number": str(args.get("order_number", "")),
        "status": args.get("status"), **({"notes": args["notes"]} if args.get("notes") else {}),
    }


HANDLERS = {
    "get_storefront_design": get_storefront_design,
    "update_storefront_design": update_storefront_design,
    "update_homepage_text": update_homepage_text,
    "add_category": add_category,
    "create_discount_event": create_discount_event,
}


async def run(name: str, args: dict, db: AsyncSession, tenant_id: uuid.UUID, handlers: dict | None = None) -> dict:
    """Runs one agent tool. A business-rule failure (plan limit, bad value,
    not found) comes back to the model as {"error": ...} so it can explain or
    adjust, instead of crashing the whole reply."""
    handler = (handlers or HANDLERS)[name]
    try:
        return await handler(db, tenant_id, **args)
    except HTTPException as exc:
        await db.rollback()
        return {"error": str(exc.detail)}
    except TypeError:
        await db.rollback()
        return {"error": "Invalid arguments for that tool."}

