"""Site media storage — Cloudinary, one folder tree per site.

WHY PER-SITE FOLDERS
---------------------
Every site's uploads live under their own folder, not one shared bucket:

    {root}/{site.subdomain}/hero/
    {root}/{site.subdomain}/products/
    {root}/{site.subdomain}/categories/
    {root}/{site.subdomain}/other/

`subdomain` is already unique per site (see Site.subdomain's unique
constraint in models.py) and human-readable, so this is both a real isolation
boundary and something you can browse by eye in the Cloudinary media library
without cross-referencing ids anywhere.

We store nothing about uploaded media in our own database — Cloudinary is the
source of truth, and the theme JSON already just holds plain URL strings
(exactly like the bundled /assets/*.jpg paths it replaces). Listing past
uploads for a site+category goes straight to Cloudinary's Admin API by folder
prefix instead of a table we'd have to keep in sync.
"""

import logging
import re

import cloudinary
import cloudinary.api
import cloudinary.exceptions
import cloudinary.uploader

log = logging.getLogger(__name__)
from fastapi import HTTPException, status

from app.config import settings

VALID_CATEGORIES = {"hero", "products", "categories", "other"}

# Cloudinary's own account-wide ceilings (free/standard plan). Enforced two
# ways: the cheap checks (file size, megapixels) run BEFORE we ever call
# Cloudinary, in app/api/media.py, so a doomed upload fails in milliseconds
# instead of after a full network round-trip. This dict is also the single
# source of truth for the human-readable limits shown in every error message
# below and in the dashboard's own upload hint text — see
# dashboard/lib/constants/media-limits.ts, which must be kept in sync.
IMAGE_MAX_BYTES = 10 * 1024 * 1024
IMAGE_MAX_MEGAPIXELS = 25
VIDEO_MAX_BYTES = 100 * 1024 * 1024

_configured = False


def _ensure_configured() -> None:
    """Configure the Cloudinary SDK lazily, on first real use.

    Doing this at import time would make the whole API fail to start for
    anyone who hasn't set up Cloudinary yet, for a feature they may not have
    touched. Doing it here means only an actual upload/list/delete call fails,
    with a message that says exactly what's missing.
    """
    global _configured
    if _configured:
        return
    if not (
        settings.cloudinary_cloud_name
        and settings.cloudinary_api_key
        and settings.cloudinary_api_secret
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Media storage isn't configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET in .env.",
        )
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    _configured = True


def folder_for(subdomain: str, category: str) -> str:
    root = settings.cloudinary_root_folder.strip("/")
    return f"{root}/{subdomain}/{category}"


def upload_image(file_bytes: bytes, *, subdomain: str, category: str) -> dict:
    _ensure_configured()
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder_for(subdomain, category),
            resource_type="image",
            unique_filename=True,
            overwrite=False,
        )
    except cloudinary.exceptions.Error as exc:
        # Cloudinary's own message is already specific ("File size too
        # large. Got 12582912. Maximum is 10485760.") — surface it instead of
        # letting this become an opaque 500. The pre-checks in
        # app/api/media.py catch the common cases (size, megapixels) before
        # this point; this is the backstop for whatever they miss (corrupt
        # file, unsupported format Cloudinary itself rejects, etc).
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "large" in str(exc).lower() or "megapixel" in str(exc).lower()
            else status.HTTP_400_BAD_REQUEST,
            f"Cloudinary rejected this file: {exc}",
        ) from exc
    return {
        "url": result["secure_url"],
        "public_id": result["public_id"],
        "width": result.get("width"),
        "height": result.get("height"),
    }


def upload_video(file_bytes: bytes, *, subdomain: str, category: str) -> dict:
    """Same shape as upload_image, resource_type=video. A separate function
    rather than a resource_type parameter on upload_image — the two error
    paths reference different Cloudinary limits (VIDEO_MAX_BYTES vs
    IMAGE_MAX_BYTES/IMAGE_MAX_MEGAPIXELS), and keeping them apart means each
    stays readable without a branch for "which limit applies here"."""
    _ensure_configured()
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder_for(subdomain, category),
            resource_type="video",
            unique_filename=True,
            overwrite=False,
        )
    except cloudinary.exceptions.Error as exc:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "large" in str(exc).lower()
            else status.HTTP_400_BAD_REQUEST,
            f"Cloudinary rejected this file: {exc}",
        ) from exc
    return {
        "url": result["secure_url"],
        "public_id": result["public_id"],
        "width": result.get("width"),
        "height": result.get("height"),
    }


def list_images(subdomain: str, category: str) -> list[dict]:
    _ensure_configured()
    folder = folder_for(subdomain, category)
    result = cloudinary.api.resources(
        type="upload", prefix=f"{folder}/", max_results=100
    )
    return [
        {
            "url": r["secure_url"],
            "public_id": r["public_id"],
            "created_at": r.get("created_at"),
            "bytes": r.get("bytes"),
            "width": r.get("width"),
            "height": r.get("height"),
        }
        for r in result.get("resources", [])
    ]


def owns(public_id: str, subdomain: str) -> bool:
    """True if `public_id` lives inside this site's own folder tree.

    Checked before every delete — public_id comes from the client, and a
    tenant-scoped site lookup alone doesn't stop someone passing a DIFFERENT
    site's public_id in the URL. The folder prefix is the real boundary.
    """
    root = settings.cloudinary_root_folder.strip("/")
    return public_id.startswith(f"{root}/{subdomain}/")


def delete_image(public_id: str) -> None:
    _ensure_configured()
    cloudinary.uploader.destroy(public_id)


# Matches ".../upload/v1712345678/{public_id}.jpg" (also handles the
# resource-type/delivery-type segments Cloudinary sometimes inserts, e.g.
# ".../image/upload/..."). Group 1 is the public_id, extension stripped.
_PUBLIC_ID_RE = re.compile(r"/upload/(?:v\d+/)?(.+?)(?:\.[a-zA-Z0-9]+)?$")


def public_id_from_url(url: str) -> str | None:
    """Recovers a Cloudinary public_id from a stored secure_url.

    Nothing in this app's database stores public_id — theme JSON and
    Product.images/Category.image_url only ever hold the plain URL string
    (see this module's top docstring: Cloudinary is the source of truth, we
    don't keep a media table). Deleting on cleanup therefore has to reverse
    the URL back into a public_id. Returns None for anything that isn't a
    Cloudinary URL at all — a bundled template asset path like
    "/assets/hero.jpg" is a real, valid value here and must be left alone,
    not passed to destroy().
    """
    if not url or "res.cloudinary.com" not in url:
        return None
    match = _PUBLIC_ID_RE.search(url)
    return match.group(1) if match else None


def theme_image_urls(theme: dict) -> set[str]:
    """Every image URL a theme JSON blob references — the fields the
    dashboard's theme editor lets a merchant upload into (logo, hero image
    sets, the Why Choose Us image, testimonial photos). Used to diff old vs
    new theme on save so a replaced/removed image can be cleaned up, and by
    the reconciliation sweep to know what's still "in use". Kept as one
    function so both call sites stay in sync with editor-types.ts's actual
    image fields instead of drifting apart.
    """
    urls: set[str] = set()
    if theme.get("logoImage"):
        urls.add(theme["logoImage"])
    if theme.get("whyImage"):
        urls.add(theme["whyImage"])
    for key in ("heroImages", "heroImagesSquare"):
        for url in theme.get(key) or []:
            if url:
                urls.add(url)
    for t in theme.get("testimonials") or []:
        if isinstance(t, dict) and t.get("image"):
            urls.add(t["image"])
    return urls


def delete_by_url(url: str, subdomain: str) -> None:
    """Best-effort cleanup for one URL that a save/delete just stopped
    referencing. Never raises — a cleanup failure (Cloudinary hiccup, an
    already-deleted file, a URL that turns out not to be ours) must not fail
    the request that's actually doing the save/delete. Same "degrade
    gracefully" instinct as cache.py's invalidation helpers.
    """
    public_id = public_id_from_url(url)
    if not public_id or not owns(public_id, subdomain):
        return
    try:
        delete_image(public_id)
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort, never fatal
        log.warning("Failed to clean up media %s: %s", public_id, exc)
