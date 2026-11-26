"""Platform-wide storage: what Cloudinary holds, folder by folder, plus the
database and the server the API runs on. Read by the superadmin Storage page.

Cloudinary's Admin API is rate limited per hour (500 on the account we run
on), and a full listing costs one call per 500 files. So the listing is kept
in memory and reused for an hour, a forced refresh is ignored within five
minutes of the last one, and the walk is capped. The API runs as one process,
so one in-memory copy is the whole cache.
"""

import asyncio
import logging
import shutil
import time
from collections.abc import Iterable
from datetime import UTC, datetime

import cloudinary.api
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, media
from app.config import settings

log = logging.getLogger(__name__)

TTL_SECONDS = 3600
MIN_REFRESH_SECONDS = 300
PAGE_SIZE = 500
MAX_PAGES = 120  # 60,000 files; past that the page says the count is partial
LARGEST = 15
# Folders under the root that are not one store's uploads.
PLATFORM_FOLDERS = {"demo": "Demo store images", "_invoices": "Invoice PDFs", "_system": "Platform files"}

_cache: dict | None = None
_cached_at = 0.0
_lock = asyncio.Lock()


def summarise(resources: Iterable[dict], root: str) -> dict:
    """Totals from a Cloudinary listing. Pure, so it can be tested without it."""
    folders: dict[str, dict] = {}
    kinds = {k: {"files": 0, "bytes": 0} for k in ("image", "video", "raw")}
    paths: set[str] = set()
    items = []
    outside = {"files": 0, "bytes": 0}
    prefix = f"{root}/"
    for r in resources:
        public_id, size = r.get("public_id", ""), r.get("bytes") or 0
        kind = r.get("resource_type") or "image"
        bucket = kinds.setdefault(kind, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += size
        if not public_id.startswith(prefix):
            outside["files"] += 1
            outside["bytes"] += size
            continue
        parts = public_id[len(prefix):].split("/")
        top = parts[0] if len(parts) > 1 else ""
        # Every folder on the way to the file, so nested ones are counted.
        for depth in range(1, len(parts)):
            paths.add("/".join(parts[:depth]))
        f = folders.setdefault(top, {"files": 0, "bytes": 0, "images": 0, "videos": 0})
        f["files"] += 1
        f["bytes"] += size
        if kind == "image":
            f["images"] += 1
        elif kind == "video":
            f["videos"] += 1
        items.append({
            "public_id": public_id, "folder": top, "bytes": size,
            "resource_type": kind, "url": r.get("secure_url"),
        })
    return {
        "folders": folders,
        "kinds": kinds,
        "folder_paths": len(paths),
        "outside_root": outside,
        "largest": sorted(items, key=lambda i: i["bytes"], reverse=True)[:LARGEST],
    }


def _account() -> dict:
    u = cloudinary.api.usage()
    part = lambda key: u.get(key) or {}  # noqa: E731
    return {
        "plan": u.get("plan"),
        "credits_used": part("credits").get("usage"),
        "credits_limit": part("credits").get("limit"),
        "credits_pct": part("credits").get("used_percent"),
        "storage_bytes": part("storage").get("usage", 0),
        "storage_credits": part("storage").get("credits_usage"),
        "bandwidth_bytes": part("bandwidth").get("usage", 0),
        "bandwidth_credits": part("bandwidth").get("credits_usage"),
        "transformations": part("transformations").get("usage", 0),
        "transformation_credits": part("transformations").get("credits_usage"),
        "requests": u.get("requests"),
        "updated": u.get("last_updated"),
        "api_calls_left": u.rate_limit_remaining,
        "api_calls_allowed": u.rate_limit_allowed,
    }


def _scan() -> dict:
    """Blocking SDK calls: run in a thread."""
    media._ensure_configured()
    root = settings.cloudinary_root_folder.strip("/")
    account = _account()
    resources: list[dict] = []
    pages, partial = 0, False
    for resource_type in ("image", "video", "raw"):
        cursor = None
        while True:
            if pages >= MAX_PAGES:
                partial = True
                break
            kwargs = {"type": "upload", "resource_type": resource_type, "max_results": PAGE_SIZE}
            if cursor:
                kwargs["next_cursor"] = cursor
            result = cloudinary.api.resources(**kwargs)
            pages += 1
            resources += result.get("resources", [])
            cursor = result.get("next_cursor")
            if not cursor:
                break
    top: list[str] = []
    cursor = None
    while True:
        kwargs = {"max_results": PAGE_SIZE}
        if cursor:
            kwargs["next_cursor"] = cursor
        result = cloudinary.api.subfolders(root, **kwargs)
        top += [f["name"] for f in result.get("folders", [])]
        cursor = result.get("next_cursor")
        if not cursor or len(top) >= 5000:
            break
    return {
        "account": account,
        "top_folders": top,
        "partial": partial,
        "scanned_at": datetime.now(UTC),
        **summarise(resources, root),
    }


async def media_report(refresh: bool = False) -> tuple[dict | None, str | None]:
    """(report, error). A Cloudinary failure keeps the last good copy."""
    global _cache, _cached_at
    async with _lock:
        age = time.monotonic() - _cached_at
        if _cache is not None and age < (MIN_REFRESH_SECONDS if refresh else TTL_SECONDS):
            return _cache, None
        try:
            _cache = await run_in_threadpool(_scan)
            _cached_at = time.monotonic()
        except Exception as exc:  # the page still shows the database and server
            log.warning("storage scan failed: %s", exc)
            return _cache, "Cloudinary couldn't be read just now."
        return _cache, None


def cached_folders() -> dict[str, dict] | None:
    """The last listing, never a new one: for lists that only decorate rows."""
    return None if _cache is None else _cache["folders"]


_refreshing: asyncio.Task | None = None


def refresh_in_background() -> None:
    """Fills or renews the listing without making the caller wait for it."""
    global _refreshing
    stale = _cache is None or time.monotonic() - _cached_at >= TTL_SECONDS
    if stale and (_refreshing is None or _refreshing.done()):
        _refreshing = asyncio.create_task(media_report())


async def database_report(db: AsyncSession) -> dict:
    total = (await db.execute(text("select pg_database_size(current_database())"))).scalar_one()
    rows = (await db.execute(text(
        "select c.relname, pg_total_relation_size(c.oid), greatest(c.reltuples, 0)::bigint "
        "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' order by 2 desc limit 12"
    ))).all()
    return {"bytes": total, "tables": [{"name": n, "bytes": b, "rows": r} for n, b, r in rows]}


def _memory() -> tuple[int | None, int | None]:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in handle}
        return values["MemTotal"], values["MemAvailable"]
    except (OSError, KeyError, ValueError, IndexError):
        return None, None  # not Linux


async def server_report() -> dict:
    disk = shutil.disk_usage("/")
    total, available = _memory()
    redis_bytes = None
    try:
        redis_bytes = int((await asyncio.wait_for(cache.client().info("memory"), 2))["used_memory"])
    except Exception:  # the cache being down must not fail the page
        pass
    return {
        "disk_total": disk.total, "disk_used": disk.used,
        "memory_total": total, "memory_available": available, "redis_bytes": redis_bytes,
    }
