"""AI store setup: a short form in, a complete published storefront out.

Why this exists: of 132 trial stores opened by the Oct 2026 campaign, none
finished the setup wizard and most never added a product — the wizard asks
for photos, copy and categories a brand-new merchant doesn't have yet. This
fills every homepage section from five answers instead.

THE FLOW (driven step by step by the dashboard, so the merchant watches it
happen and a closed tab resumes where it stopped):
  1. plan(inputs)      — one Gemini text call writes every word of the site
                         as structured JSON. Capped per tenant
                         (settings.ai_site_setup_max_runs).
  2. image(slot) x 5   — hero, two category tiles, the product, the
                         why-choose-us photo. Paid ONLY from the tenant's
                         free image allowance (never ai_image_credits); when
                         that runs out a slot is skipped, never charged.
  3. apply()           — creates the categories and the product, makes sure
                         Cash on Delivery is on, writes the theme, publishes,
                         and marks onboarding finished.

Every write goes through the same route handlers the dashboard itself calls
(create_category, create_product, connect_payment, update_site,
publish_site), so validation, plan limits, cache invalidation and
revalidation are identical to a merchant doing it by hand.

State lives in tenant.settings["ai_site_setup"] so a reload, a second
device, or a failure halfway through picks up from the last finished step
instead of spending free images twice.
"""

import asyncio
import html
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_images, ai_log, media
from app.config import settings
from app.models import PaymentConnection, Site, Tenant

log = logging.getLogger(__name__)

STATE_KEY = "ai_site_setup"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Order = generation order = what the merchant sees fill in first.
IMAGE_SLOTS = ("hero", "category_0", "category_1", "product", "why")

# Real Iconify ids from dashboard/lib/solar-icons.ts (all verified present),
# the format the storefronts' FeatureIcon renders. The model may only pick
# from these — an invented id would render as a blank square.
FEATURE_ICONS = [
    "solar:delivery-bold",
    "solar:shield-check-bold",
    "solar:wallet-bold",
    "solar:hand-money-bold",
    "solar:star-bold",
    "solar:medal-star-bold",
    "solar:heart-bold",
    "solar:box-bold",
    "solar:gift-bold",
    "solar:chat-round-dots-bold",
    "solar:phone-calling-bold",
    "solar:refresh-bold",
    "solar:verified-check-bold",
    "solar:clock-circle-bold",
    "solar:tag-bold",
    "solar:bag-check-bold",
    "solar:leaf-bold",
    "solar:like-bold",
]


# =============================================================================
#  State
# =============================================================================


def get_state(tenant: Tenant) -> dict:
    return dict((tenant.settings or {}).get(STATE_KEY) or {})


async def save_state(db: AsyncSession, tenant: Tenant, state: dict) -> None:
    # Reassign the whole JSONB value: SQLAlchemy doesn't see in-place edits
    # to a dict column, so mutating tenant.settings[...] would silently not save.
    state["updated_at"] = datetime.now(UTC).isoformat()
    tenant.settings = {**(tenant.settings or {}), STATE_KEY: state}
    await db.commit()


async def load_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    return (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()


async def first_site(db: AsyncSession, tenant_id: uuid.UUID) -> Site:
    site = (
        await db.execute(
            select(Site).where(Site.tenant_id == tenant_id).order_by(Site.created_at).limit(1)
        )
    ).scalars().first()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No store found for this account.")
    return site


def public_state(state: dict, free_remaining: int) -> dict:
    """What the dashboard needs to render or resume the flow. No store URL:
    the dashboard already derives the real host (displayStorefrontHost), and
    a second copy here could disagree with it."""
    return {
        "status": state.get("status") or "none",
        "inputs": state.get("inputs"),
        "plan": state.get("plan"),
        "images": state.get("images") or {},
        "skipped": state.get("skipped") or [],
        "runs_used": state.get("runs", 0),
        "runs_limit": settings.ai_site_setup_max_runs,
        "free_images_remaining": free_remaining,
    }


# =============================================================================
#  Step 1: plan — every word of the site, one call
# =============================================================================

_PLAN_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "tagline": {"type": "STRING"},
        "announcement_items": {"type": "ARRAY", "items": {"type": "STRING"}},
        "hero": {
            "type": "OBJECT",
            "properties": {
                "headline": {"type": "STRING"},
                "subline": {"type": "STRING"},
                "button": {"type": "STRING"},
                "image_subject": {"type": "STRING"},
            },
            "required": ["headline", "subline", "button", "image_subject"],
        },
        "categories_title": {"type": "STRING"},
        "categories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "image_subject": {"type": "STRING"},
                },
                "required": ["name", "description", "image_subject"],
            },
        },
        "product": {
            "type": "OBJECT",
            "properties": {
                "short_description": {"type": "STRING"},
                "description_paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}},
                "features": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "description": {"type": "STRING"},
                        },
                        "required": ["title", "description"],
                    },
                },
                "image_subject": {"type": "STRING"},
            },
            "required": ["short_description", "description_paragraphs", "features", "image_subject"],
        },
        "featured_products_title": {"type": "STRING"},
        "category_showcase_title": {"type": "STRING"},
        "why": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "items": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {"title": {"type": "STRING"}, "text": {"type": "STRING"}},
                        "required": ["title", "text"],
                    },
                },
                "image_subject": {"type": "STRING"},
            },
            "required": ["title", "items", "image_subject"],
        },
        "features": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "items": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "text": {"type": "STRING"},
                            "icon": {"type": "STRING", "enum": FEATURE_ICONS},
                        },
                        "required": ["title", "text", "icon"],
                    },
                },
            },
            "required": ["title", "items"],
        },
        "cta": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "body": {"type": "STRING"},
                "button": {"type": "STRING"},
            },
            "required": ["title", "body", "button"],
        },
        "footer_description": {"type": "STRING"},
        "meta_description": {"type": "STRING"},
    },
    "required": [
        "tagline", "announcement_items", "hero", "categories_title", "categories",
        "product", "featured_products_title", "category_showcase_title", "why",
        "features", "cta", "footer_description", "meta_description",
    ],
}


def _plan_prompt(inputs: dict) -> str:
    language = "Bangla (Bengali script)" if inputs["language"] == "bn" else "English"
    given_categories = [c for c in inputs.get("category_names") or [] if c.strip()]
    categories_line = (
        f"Use exactly these two category names, in this order: {json.dumps(given_categories, ensure_ascii=False)} "
        "(write only their descriptions)."
        if len(given_categories) == 2
        else (
            f"The first category must be named {json.dumps(given_categories[0], ensure_ascii=False)}; "
            "invent one more that fits the shop."
            if len(given_categories) == 1
            else "Invent two category names that fit what the shop sells."
        )
    )
    notes = (inputs.get("notes") or "").strip()
    return f"""You write the complete homepage copy for a brand-new online shop in Bangladesh,
built on Softune. Write like a skilled local copywriter: short, warm, specific to
what this shop sells, never generic filler.

SHOP
- Name: {inputs['shop_name']}
- Sells: {inputs['sells']}
- First product: {inputs['product_name']} (price ৳{inputs['product_price_taka']:,})
- {categories_line}
- Notes from the owner: {notes or '(none)'}

LANGUAGE: write every customer-facing string in {language}. Keep the shop name and
product name exactly as given. Every "image_subject" field is the ONLY exception:
write those in English, as a short visual description for an image generator
(what is in the picture — no text, no logos, no brand names).

HONESTY — this is a real shop's real website:
- Never invent facts: no exact delivery times, free delivery, warranties,
  "100% original", discounts, ratings, customer counts, years in business,
  awards or certifications — unless the owner's notes state them.
- Cash on Delivery IS available, so it may be mentioned.
- No fake reviews or testimonials anywhere.

LENGTHS (hard limits — count characters):
tagline ≤ 70 · each announcement item ≤ 55 (exactly 3) · hero headline ≤ 40,
subline ≤ 90, button ≤ 18 · section titles ≤ 40 · category description ≤ 120
(exactly 2 categories) · product short_description ≤ 150, 2 description
paragraphs ≤ 260 each, exactly 3 product features (title ≤ 28, description ≤ 90)
· why title ≤ 40 with exactly 3 items (title ≤ 28, text ≤ 110) · features title
≤ 40 with exactly 3 items (title ≤ 26, text ≤ 70, icon from the allowed list) ·
cta title ≤ 50, body ≤ 120, button ≤ 18 · footer_description ≤ 150 ·
meta_description ≤ 155 (for Google, mention the shop name and what it sells).
"""


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sanitize_plan(raw: dict, inputs: dict) -> dict:
    """Clamp every field to what the storefront layouts can hold, enforce the
    exact item counts, and put the owner's own category names back verbatim
    even if the model "improved" them. Garbage in a field becomes an empty
    string, never an exception — one odd field shouldn't sink the whole run."""
    raw = raw if isinstance(raw, dict) else {}

    def obj(key: str) -> dict:
        v = raw.get(key)
        return v if isinstance(v, dict) else {}

    def items(v: Any, n: int) -> list:
        lst = [x for x in (v if isinstance(v, list) else []) if x]
        return (lst + [{}] * n)[:n]

    hero, product, why, features, cta = (obj(k) for k in ("hero", "product", "why", "features", "cta"))
    given = [c.strip() for c in inputs.get("category_names") or [] if c and c.strip()]
    categories = []
    for i, c in enumerate(items(raw.get("categories"), 2)):
        c = c if isinstance(c, dict) else {}
        name = given[i] if i < len(given) else _clip(c.get("name"), 40)
        categories.append({
            "name": name or ("Collection" if i == 0 else "New arrivals"),
            "description": _clip(c.get("description"), 120),
            "image_subject": _clip(c.get("image_subject"), 160) or name,
        })

    icons_ok = set(FEATURE_ICONS)
    feature_items = []
    for i, f in enumerate(items(features.get("items"), 3)):
        f = f if isinstance(f, dict) else {}
        icon = f.get("icon") if f.get("icon") in icons_ok else FEATURE_ICONS[i]
        feature_items.append({"title": _clip(f.get("title"), 26), "text": _clip(f.get("text"), 70), "icon": icon})

    return {
        "tagline": _clip(raw.get("tagline"), 70),
        "announcement_items": [
            _clip(a, 55) for a in items(raw.get("announcement_items"), 3) if isinstance(a, str) and a.strip()
        ],
        "hero": {
            "headline": _clip(hero.get("headline"), 40),
            "subline": _clip(hero.get("subline"), 90),
            "button": _clip(hero.get("button"), 18),
            "image_subject": _clip(hero.get("image_subject"), 160) or inputs["sells"],
        },
        "categories_title": _clip(raw.get("categories_title"), 40),
        "categories": categories,
        "product": {
            "name": inputs["product_name"],
            "price_taka": inputs["product_price_taka"],
            "short_description": _clip(product.get("short_description"), 150),
            "description_paragraphs": [
                _clip(p, 260)
                for p in items(product.get("description_paragraphs"), 2)
                if isinstance(p, str) and p.strip()
            ],
            "features": [
                {"title": _clip(f.get("title"), 28), "description": _clip(f.get("description"), 90)}
                for f in items(product.get("features"), 3)
                if isinstance(f, dict) and f.get("title")
            ],
            "image_subject": _clip(product.get("image_subject"), 160) or inputs["product_name"],
        },
        "featured_products_title": _clip(raw.get("featured_products_title"), 40),
        "category_showcase_title": _clip(raw.get("category_showcase_title"), 40),
        "why": {
            "title": _clip(why.get("title"), 40),
            "items": [
                {"title": _clip(w.get("title"), 28), "text": _clip(w.get("text"), 110)}
                for w in (x if isinstance(x, dict) else {} for x in items(why.get("items"), 3))
            ],
            "image_subject": _clip(why.get("image_subject"), 160) or inputs["sells"],
        },
        "features": {"title": _clip(features.get("title"), 40), "items": feature_items},
        "cta": {
            "title": _clip(cta.get("title"), 50),
            "body": _clip(cta.get("body"), 120),
            "button": _clip(cta.get("button"), 18),
        },
        "footer_description": _clip(raw.get("footer_description"), 150),
        "meta_description": _clip(raw.get("meta_description"), 155),
    }


async def _gemini_json(model: str, prompt: str) -> httpx.Response:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _PLAN_SCHEMA,
            "temperature": 0.7,
        },
    }
    url = GEMINI_URL.format(model=model)
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, params={"key": settings.gemini_api_key}, json=body)
        for delay in (2.0, 5.0):
            if res.status_code not in (429, 503):
                break
            await asyncio.sleep(delay)
            res = await client.post(url, params={"key": settings.gemini_api_key}, json=body)
    ai_log.record_gemini(res, model)
    return res


async def generate_plan(inputs: dict) -> dict:
    if not settings.gemini_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI isn't configured yet.")
    prompt = _plan_prompt(inputs)
    try:
        res = await _gemini_json(settings.ai_site_builder_model, prompt)
        if res.status_code in (400, 404) and settings.ai_site_builder_model != settings.gemini_model:
            # The configured "best" model id was rejected (renamed/retired) —
            # a site still gets built, just on the chat's own model.
            log.warning("site builder model %s rejected (%s); falling back", settings.ai_site_builder_model, res.status_code)
            res = await _gemini_json(settings.gemini_model, prompt)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't reach the AI service. Try again.") from exc
    if res.status_code != 200:
        log.warning("site plan failed: %s %s", res.status_code, res.text[:400])
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The AI couldn't write your site right now. Try again.")
    try:
        text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI returned something unusable. Try again.") from exc
    return _sanitize_plan(raw, inputs)


# =============================================================================
#  Step 2: images — free allowance only
# =============================================================================

def image_request(slot: str, plan: dict, inputs: dict) -> tuple[dict, str]:
    """(GenerateImageIn kwargs, Cloudinary category) for one slot. Reuses the
    tested presets from app/ai_image_presets.py where one fits, so these
    images match what a merchant would get generating by hand."""
    sells = inputs["sells"]
    if slot == "hero":
        hero = plan["hero"]
        return (
            {
                "preset_id": "hero-studio-gradient",
                "subject": _clip(hero["image_subject"] or sells, 200),
                "aspect_ratio": "16:9",
                "include_text": True,
                "headline": hero["headline"] or None,
                "description": hero["subline"] or None,
                "button_text": hero["button"] or None,
                "prompt": (
                    "Keep the subject and any text well inside the central area, because "
                    "the storefront crops this banner slightly at the top and bottom, and "
                    "on phones it is shown center-cropped."
                ),
            },
            "hero",
        )
    if slot.startswith("category_"):
        cat = plan["categories"][int(slot.split("_")[1])]
        return (
            {
                "preset_id": "category-pastel-spotlight",
                "subject": _clip(cat["image_subject"] or cat["name"], 200),
                "aspect_ratio": "1:1",
                "include_text": False,
            },
            "categories",
        )
    if slot == "product":
        p = plan["product"]
        return (
            {
                "prompt": (
                    f"A clean, professional e-commerce product photo of {p['image_subject']}: "
                    "the product centered and fully in frame on a soft, plain light background, "
                    "bright even studio lighting, gentle natural shadow, sharp focus, true-to-life "
                    "colors, catalog style for an online shop."
                ),
                "aspect_ratio": "1:1",
                "include_text": False,
            },
            "products",
        )
    if slot == "why":
        return (
            {
                "preset_id": "why-careful-packing",
                "subject": _clip(plan["why"]["image_subject"] or sells, 200),
                "aspect_ratio": "1:1",
                "include_text": False,
            },
            "other",
        )
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown image slot")


async def store_image(site: Site, tenant: Tenant, image_bytes: bytes, category: str) -> str:
    limit = media.plan_storage_limit(tenant.plan)
    used = await run_in_threadpool(media.site_storage_used_bytes, site.subdomain)
    if used + len(image_bytes) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Your media storage is full, so the AI can't save more images.",
        )
    uploaded = await run_in_threadpool(
        media.upload_image, image_bytes, subdomain=site.subdomain, category=category
    )
    return uploaded["url"]


async def generate_free_image(
    db: AsyncSession, tenant_id: uuid.UUID, prompt: str, aspect_ratio: str
) -> tuple[bytes, str, int]:
    """One standard-tier image paid from the free allowance — claimed
    atomically first, given back if Gemini fails (ai_images' own rule)."""
    remaining = await ai_images.claim_onboarding_free(db, tenant_id)
    try:
        image_bytes, mime_type = await ai_images.generate_image(prompt, "standard", None, aspect_ratio)
    except Exception:
        await ai_images.release_onboarding_free(db, tenant_id)
        raise
    return image_bytes, mime_type, remaining


# =============================================================================
#  Step 3: apply — theme fields
# =============================================================================


def theme_patch(plan: dict, inputs: dict, images: dict, category_ids: list[str], product_id: str | None) -> dict:
    """Only keys the theme editor's SiteEditorSettings already defines
    (dashboard/components/themes/editor/editor-types.ts), so the editor opens
    the result like any hand-made theme. Testimonials are left alone on
    purpose: an AI must never write reviews nobody gave."""
    why_items = plan["why"]["items"]
    feature_items = plan["features"]["items"]
    patch: dict[str, Any] = {
        "siteName": inputs["shop_name"],
        "tagline": plan["tagline"],
        "categoriesTitle": plan["categories_title"],
        "selectedCategoryIds": [],
        "excludedCategoryIds": [],
        "featureProductsTitle": plan["featured_products_title"],
        "categoryShowcaseTitle": plan["category_showcase_title"],
        "categoryShowcaseCategoryIds": category_ids,
        "whyTitle": plan["why"]["title"],
        "featuresTitle": plan["features"]["title"],
        "ctaTitle": plan["cta"]["title"],
        "ctaBody": plan["cta"]["body"],
        "ctaButton": plan["cta"]["button"],
        "footerDescription": plan["footer_description"],
    }
    if plan["announcement_items"]:
        patch["announcementItems"] = plan["announcement_items"]
    if images.get("hero"):
        patch["heroMediaType"] = "image"
        patch["heroImages"] = [images["hero"]]
        # Empty on purpose: every template falls back to the 16:9 hero on
        # phones, and the free allowance is better spent on a fifth image.
        patch["heroImagesSquare"] = []
    if images.get("why"):
        patch["whyImage"] = images["why"]
    if product_id:
        patch["selectedProductIds"] = [product_id]
        patch["showcaseProductId"] = product_id
    for n, item in enumerate(why_items[:3], start=1):
        patch[f"why{n}Title"] = item["title"]
        patch[f"why{n}"] = item["text"]
    for n, item in enumerate(feature_items[:3], start=1):
        patch[f"feature{n}Title"] = item["title"]
        patch[f"feature{n}"] = item["text"]
        patch[f"feature{n}IconKind"] = "icon"
        patch[f"feature{n}Icon"] = item["icon"]
    return patch


def product_description_html(plan: dict) -> str:
    return "".join(f"<p>{html.escape(p)}</p>" for p in plan["product"]["description_paragraphs"])


async def has_payment_method(db: AsyncSession, site: Site) -> bool:
    return (
        await db.execute(select(PaymentConnection.id).where(PaymentConnection.site_id == site.id).limit(1))
    ).first() is not None
