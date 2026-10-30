"""Images for the AI agent: letting it SEE a picture the merchant attached, and
PLACE it on the store (hero, why-choose-us image, a category, a product).

Flow: the dashboard uploads each attached image to the merchant's own media
library first (the normal upload endpoint, so size limits and plan storage
caps apply), then sends the hosted URL with the message. This module
  * checks the URL really is one of THIS store's uploads — the agent must
    never place, or fetch, an arbitrary address (no hotlinking, no probing
    internal hosts, no using another store's photos);
  * downloads a small version to show the model (inline image bytes);
  * provides the tools that put a hosted image on the storefront.
Placement reuses ai_agent_tools' theme-change and Undo machinery.
"""

import base64
import logging
import re
import uuid
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app import ai_actions, ai_agent_tools, cache, crud
from app.config import settings
from app.models import Category, Product, Site

log = logging.getLogger(__name__)

MAX_IMAGES_PER_MESSAGE = 4
_MAX_FETCH_BYTES = 3 * 1024 * 1024
_MAX_PRODUCT_IMAGES = 8
_URL_OK = re.compile(r"^https://res\.cloudinary\.com/[A-Za-z0-9_-]+/image/upload/[A-Za-z0-9_./,:%~-]+$")


def own_image_url(site: Site, url: Any) -> str:
    """Returns `url` if it is a Cloudinary image in THIS store's folder
    (…/<root>/<subdomain>/…), else raises. Called before the URL is fetched
    or saved anywhere."""
    text = str(url or "").strip()
    folder = f"/{settings.cloudinary_root_folder.strip('/')}/{site.subdomain}/"
    ok = (
        _URL_OK.match(text)
        and ".." not in text
        and f"/{settings.cloudinary_cloud_name}/" in text
        and folder in text
    )
    if not ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That image isn't in this store's media library, so I can't use it. "
            "Attach it to the chat or upload it in Media first.",
        )
    return text


def _preview_url(url: str) -> str:
    # Cloudinary resizes on the fly: ~1k px is plenty to read a screenshot or
    # judge a photo and keeps both the download and the model's image tokens
    # small (about 1,000 tokens per image, a fraction of a cent).
    return url.replace("/image/upload/", "/image/upload/w_1024,h_1024,c_limit,q_auto,f_jpg/", 1)


async def load_for_model(site: Site, images: list[dict]) -> tuple[list[dict], list[str]]:
    """(inline image parts for Gemini, notes about images that couldn't be
    loaded). A bad or unreachable image never fails the whole message."""
    parts: list[dict] = []
    notes: list[str] = []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as http:
        for index, image in enumerate(images[:MAX_IMAGES_PER_MESSAGE], start=1):
            try:
                url = own_image_url(site, image.get("url"))
                res = await http.get(_preview_url(url))
                if res.status_code != 200 or len(res.content) > _MAX_FETCH_BYTES:
                    raise ValueError(f"fetch status {res.status_code}")
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(res.content).decode()}})
                notes.append(f"[Image {index}: \"{str(image.get('name') or 'image')[:60]}\" — hosted at {url}]")
            except Exception as exc:  # noqa: BLE001 - see docstring
                log.warning("agent image %s not loaded: %s", index, exc)
                notes.append(f"[Image {index} could not be loaded]")
    return parts, notes


# --- tools ----------------------------------------------------------------

DECLARATIONS: list[dict] = [
    {
        "name": "set_homepage_image",
        "description": "Put an attached image on the homepage: the main hero banner (wide, for desktop), the mobile hero (square or portrait, shown on phones) or the why-choose-us section. Applied immediately; undo restores it.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "slot": {"type": "STRING", "enum": ["hero", "hero_mobile", "why"]},
                "image_url": {"type": "STRING", "description": "Exact hosted URL from the attached image note."},
            },
            "required": ["slot", "image_url"],
        },
    },
    {
        "name": "set_category_image",
        "description": "Set a category's picture from an attached image. Applied immediately; undo restores it.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"category_name": {"type": "STRING"}, "image_url": {"type": "STRING"}},
            "required": ["category_name", "image_url"],
        },
    },
    {
        "name": "add_product_image",
        "description": "Add an attached image to a product's photos (first photo is the cover). Applied immediately; undo restores it.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "product_name": {"type": "STRING"},
                "product_id": {"type": "STRING"},
                "image_url": {"type": "STRING"},
                "make_cover": {"type": "BOOLEAN", "description": "Put it first."},
            },
            "required": ["image_url"],
        },
    },
]


async def set_homepage_image(db: AsyncSession, tenant_id: uuid.UUID, slot: str, image_url: str) -> dict:
    site = await ai_agent_tools._site(db, tenant_id)
    url = own_image_url(site, image_url)
    if slot == "hero":
        # heroImagesSquare stays as is: phones fall back to the 16:9 set
        # when it's empty, and a merchant-set mobile image is theirs to keep.
        patch = {"heroMediaType": "image", "heroImages": [url]}
        summary = "Set the hero banner image"
    elif slot == "hero_mobile":
        # The square/portrait set phones use instead of the wide banner.
        patch = {"heroImagesSquare": [url]}
        summary = "Set the mobile hero image"
    elif slot == "why":
        patch, summary = {"whyImage": url}, "Set the why-choose-us image"
    else:
        return {"error": "slot must be hero, hero_mobile or why"}
    return await ai_agent_tools._theme_change(db, tenant_id, "set_homepage_image", patch, summary)


async def set_category_image(db: AsyncSession, tenant_id: uuid.UUID, category_name: str, image_url: str) -> dict:
    site = await ai_agent_tools._site(db, tenant_id)
    url = own_image_url(site, image_url)
    category_id = await ai_actions._resolve_category_id(db, site, category_name)
    if category_id is None:
        return {"error": f"No category named “{category_name}”. Check the category list first."}
    category = await crud.get_scoped(db, Category, tenant_id, category_id)
    before = category.image_url
    category.image_url = url
    await crud.save(db, category)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    change = await ai_agent_tools._record(
        db, site, "set_category_image", f"Set the picture for “{category.name}”",
        {"type": "restore_category", "category_id": str(category.id), "before": {"image_url": before}},
    )
    await ai_agent_tools._forget_tool_cache(tenant_id)
    return {"ok": True, "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}


async def add_product_image(
    db: AsyncSession, tenant_id: uuid.UUID, image_url: str,
    product_name: str | None = None, product_id: str | None = None, make_cover: bool = False,
) -> dict:
    site = await ai_agent_tools._site(db, tenant_id)
    url = own_image_url(site, image_url)
    product = await ai_actions._find_product(db, site, product_id, product_name)
    before = list(product.images or [])
    if any(i.get("url") == url for i in before):
        return {"ok": True, "unchanged": True, "message": "That image is already on the product."}
    if len(before) >= _MAX_PRODUCT_IMAGES:
        return {"error": f"A product can have up to {_MAX_PRODUCT_IMAGES} photos."}
    new = {"url": url, "public_id": None}
    product.images = ([new] + before) if (make_cover or not before) else (before + [new])
    flag_modified(product, "images")
    await crud.save(db, product)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    change = await ai_agent_tools._record(
        db, site, "add_product_image", f"Added a photo to “{product.name}”",
        {"type": "restore_product", "product_id": str(product.id), "before": {"images": before}},
    )
    await ai_agent_tools._forget_tool_cache(tenant_id)
    return {"ok": True, "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}


HANDLERS = {
    "set_homepage_image": set_homepage_image,
    "set_category_image": set_category_image,
    "add_product_image": add_product_image,
}
