"""Lets a connected ChatGPT put an image it made (or one the merchant attached
in the chat) into the store's media library.

ChatGPT hands the tool a temporary download link for the file (the tool's
`openai/fileParams` declaration, see app/mcp_server.py). Fetching a URL that
someone else supplied is the risky part, so it is locked down:
  * https only, and the host must be on settings.mcp_image_hosts (OpenAI's file
    hosts) — never an arbitrary address, so it can't be aimed at our own
    network or another site;
  * no redirects (a redirect is how an allowed host could bounce us elsewhere);
  * streamed with a hard size cap, then run through the same size, megapixel
    and plan-storage checks as a normal upload (app/api/ai_images.py's
    save_to_gallery) before anything reaches Cloudinary.
Adding to the library never overwrites anything, so it has no Undo; placing the
image on the storefront goes through the existing set_* tools, which do.
"""

import io
import logging
import re
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_agent_tools, media
from app.config import settings
from app.models import Tenant

log = logging.getLogger(__name__)


def _allowed_host(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.port not in (None, 443) or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in settings.mcp_image_hosts) or any(
        re.match(pattern, host) for pattern in settings.mcp_image_host_patterns
    )


async def _download(url: str) -> bytes:
    if not _allowed_host(url):
        # Host only, never the full link (it can carry a signed token).
        log.warning("mcp image link rejected: scheme=%s host=%s", urlparse(url).scheme, urlparse(url).hostname)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That image link isn't from ChatGPT, so I can't use it.")
    limit = media.IMAGE_MAX_BYTES
    chunks: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
            async with http.stream("GET", url) as res:
                if res.status_code != 200:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Couldn't download that image from ChatGPT. Try attaching it again.")
                async for chunk in res.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"That image is over {limit // 1024 // 1024}MB. Ask for a smaller one.",
                        )
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Couldn't download that image from ChatGPT.") from exc
    return b"".join(chunks)


async def add_image_to_gallery(db: AsyncSession, tenant_id, image: dict | None = None) -> dict:
    """Stores the image in this store's media library and returns its URL, which
    set_homepage_image / set_category_image / add_product_image then accept."""
    url = (image or {}).get("download_url") if isinstance(image, dict) else None
    if not url:
        return {"error": "No image was attached. Ask the merchant to attach or generate one."}
    data = await _download(str(url))
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").upper()
    except Exception:  # noqa: BLE001 - any decode failure means "not a real image"
        return {"error": "That file isn't a valid image."}
    if fmt not in {"JPEG", "PNG", "WEBP", "AVIF"}:
        return {"error": "Only JPEG, PNG, WebP or AVIF images can be added."}
    if (width * height) / 1_000_000 > media.IMAGE_MAX_MEGAPIXELS:
        return {"error": f"That image is too large ({width}×{height}). Ask for a smaller one."}

    site = await ai_agent_tools._site(db, tenant_id)
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    if media.site_storage_used_bytes(site.subdomain) + len(data) > media.plan_storage_limit(tenant.plan):
        return {"error": "This store's media storage is full. Delete unused media in the dashboard first."}

    uploaded = await run_in_threadpool(media.upload_image, data, subdomain=site.subdomain, category="other")
    return {
        "ok": True,
        "image_url": uploaded["url"],
        "width": uploaded.get("width"),
        "height": uploaded.get("height"),
        "next": "Use this image_url with set_homepage_image, set_category_image or add_product_image to put it on the store.",
    }
