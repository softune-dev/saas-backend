"""Per-site media uploads (Cloudinary). See app/media.py for the storage layout.

No new table: Cloudinary is the source of truth, and callers (the theme
editor) just store the returned URL string directly in theme JSON, the same
way they already store the bundled /assets/*.jpg paths it replaces.
"""

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, media
from app.db import get_db
from app.media import IMAGE_MAX_BYTES, IMAGE_MAX_MEGAPIXELS, VALID_CATEGORIES, VIDEO_MAX_BYTES
from app.models import Category as CategoryModel
from app.models import Product, Site
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/media", tags=["media"])
DB = Annotated[AsyncSession, Depends(get_db)]

# Mirrors Cloudinary's own account-wide image limits (see app/media.py) —
# checking here means a doomed upload fails in milliseconds, before ever
# reaching the network, with a message that says exactly which limit it hit.
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}

Category = Annotated[
    str, Query(pattern="^(hero|products|categories|other)$")
]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_media(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    category: Category,
    file: UploadFile = File(...),
) -> dict:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Only JPEG, PNG, WebP, or AVIF images are accepted.",
        )

    body = await file.read()
    if len(body) > IMAGE_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"This image is {len(body) / 1024 / 1024:.1f}MB. Cloudinary's limit "
            f"is {IMAGE_MAX_BYTES // 1024 // 1024}MB — try a smaller file or "
            "compress it first.",
        )

    # Decode just enough to read dimensions, not the full image — cheap, and
    # catches an oversized/corrupt photo before spending a network round-trip
    # on an upload that Cloudinary would reject anyway.
    try:
        with Image.open(io.BytesIO(body)) as img:
            width, height = img.size
    except Exception as exc:  # noqa: BLE001 - any decode failure means "not a real image"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This file doesn't look like a valid image.",
        ) from exc

    megapixels = (width * height) / 1_000_000
    if megapixels > IMAGE_MAX_MEGAPIXELS:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"This image is {width}×{height} ({megapixels:.1f} megapixels). "
            f"Cloudinary's limit is {IMAGE_MAX_MEGAPIXELS} megapixels — resize "
            "it down before uploading.",
        )

    return media.upload_image(body, subdomain=site.subdomain, category=category)


@router.post("/video", status_code=status.HTTP_201_CREATED)
async def upload_video(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    category: Category,
    file: UploadFile = File(...),
) -> dict:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    if file.content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Only MP4, WebM, or MOV videos are accepted.",
        )

    body = await file.read()
    if len(body) > VIDEO_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"This video is {len(body) / 1024 / 1024:.1f}MB. Cloudinary's "
            f"limit is {VIDEO_MAX_BYTES // 1024 // 1024}MB — compress it or "
            "use a shorter clip.",
        )

    return media.upload_video(body, subdomain=site.subdomain, category=category)


@router.get("")
async def list_media(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    category: Category,
) -> list[dict]:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    return media.list_images(site.subdomain, category)


@router.delete("/{public_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    site_id: uuid.UUID,
    public_id: str,
    user: CurrentUser,
    db: DB,
) -> None:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    if not media.owns(public_id, site.subdomain):
        # Not this site's folder — 404, not 403, same reasoning as
        # crud.get_scoped: don't confirm whether the id is real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    media.delete_image(public_id)


# Anything uploaded more recently than this is left alone even if nothing
# references it yet — the theme editor's image pickers (hero, logo, why-us,
# testimonials) upload to Cloudinary the moment a file is chosen, well before
# the merchant clicks Save/Publish. Without this window, a sweep running
# mid-edit would delete an image the merchant is actively about to use.
CLEANUP_GRACE = timedelta(hours=24)


async def _referenced_urls(db: AsyncSession, site: Site) -> set[str]:
    """Every image URL currently pointed at by something real on this site —
    theme (logo/hero/why-us/testimonials), site-wide SEO (OG image, favicon),
    business info (logo), every category's image, every product's gallery.
    Shared by the cleanup sweep and the gallery's "in use" badge so both
    agree on what "in use" means.
    """
    referenced = set(media.theme_image_urls(site.theme or {}))
    seo = site.seo or {}
    if seo.get("og_image"):
        referenced.add(seo["og_image"])
    if seo.get("favicon"):
        referenced.add(seo["favicon"])
    business = site.business or {}
    if business.get("logo_url"):
        referenced.add(business["logo_url"])

    categories = (
        await db.execute(select(CategoryModel).where(CategoryModel.site_id == site.id))
    ).scalars().all()
    referenced.update(c.image_url for c in categories if c.image_url)
    referenced.update(c.banner_url for c in categories if c.banner_url)

    products = (
        await db.execute(select(Product).where(Product.site_id == site.id))
    ).scalars().all()
    for p in products:
        referenced.update(
            img["url"] for img in (p.images or []) if isinstance(img, dict) and img.get("url")
        )
    return referenced


@router.get("/all")
async def list_all_media(site_id: uuid.UUID, user: CurrentUser, db: DB) -> dict:
    """Every uploaded image across all four categories, with size/dimensions
    and whether each one is still referenced anywhere on the site — powers
    the Media settings gallery (grid, per-category stats, "in use" warning
    before a delete).
    """
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    referenced = await _referenced_urls(db, site)

    images: list[dict] = []
    by_category: dict[str, dict] = {}
    for category in sorted(VALID_CATEGORIES):
        resources = media.list_images(site.subdomain, category)
        by_category[category] = {
            "count": len(resources),
            "bytes": sum(r.get("bytes") or 0 for r in resources),
        }
        for r in resources:
            images.append(
                {
                    **r,
                    "category": category,
                    "in_use": r["url"] in referenced,
                }
            )

    images.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {
        "images": images,
        "total_count": len(images),
        "total_bytes": sum(r.get("bytes") or 0 for r in images),
        "by_category": by_category,
    }


@router.post("/cleanup")
async def cleanup_media(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
) -> dict:
    """Reconciliation sweep: deletes every file in this site's Cloudinary
    folders that nothing in the database currently references and that
    wasn't uploaded in the last 24h.

    This is the catch-up mechanism for images that piled up BEFORE eager
    cleanup existed on delete/replace (see commerce.py, sites.py, and
    ai_actions.py, which now clean up as-they-go) — and a permanent safety
    net for anything that logic misses, since it reasons from "what's
    actually referenced right now" rather than replaying every past edit.
    """
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    referenced = await _referenced_urls(db, site)

    cutoff = datetime.now(UTC) - CLEANUP_GRACE
    checked = 0
    deleted: list[str] = []
    for category in sorted(VALID_CATEGORIES):
        for resource in media.list_images(site.subdomain, category):
            checked += 1
            if resource["url"] in referenced:
                continue
            created_at = resource.get("created_at")
            if created_at and datetime.fromisoformat(created_at) > cutoff:
                continue
            media.delete_by_url(resource["url"], site.subdomain)
            deleted.append(resource["url"])

    return {"checked": checked, "deleted": len(deleted), "deleted_urls": deleted}
