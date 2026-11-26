"""The superadmin Storage page: Cloudinary, database and server in one call.

Read-only and cross-tenant, gated by SuperAdminUser like the rest of
/superadmin. Nothing here deletes a file: a folder with no matching store is
only pointed out, because a theme may still be showing an image from it.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans, storage_report
from app.db import get_db
from app.models import Site, Tenant
from app.schemas import ORMModel
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin", tags=["superadmin-storage"])
DB = Annotated[AsyncSession, Depends(get_db)]

# "Near its limit" starts at 80% of the plan's per-store storage.
NEAR = 0.8


class StoreStorageOut(ORMModel):
    folder: str
    tenant_id: str
    name: str
    plan: str
    bytes: int
    files: int
    images: int
    videos: int
    limit_bytes: int
    pct: float


class OtherFolderOut(ORMModel):
    folder: str
    # "platform" is ours on purpose; "unmatched" has no store of that name.
    kind: str
    label: str
    bytes: int
    files: int


class StorageOut(ORMModel):
    scanned_at: datetime | None
    partial: bool
    error: str | None
    account: dict | None
    totals: dict[str, int]
    stores: list[StoreStorageOut]
    other_folders: list[OtherFolderOut]
    largest: list[dict]
    database: dict
    server: dict


def split_folders(folders: dict[str, dict], top_folders: list[str], sites: list[tuple]) -> tuple[list, list]:
    """(stores, other folders). `sites` is (subdomain, tenant_id, name, plan)."""
    empty = {"files": 0, "bytes": 0, "images": 0, "videos": 0}
    stores = []
    for subdomain, tenant_id, name, plan in sites:
        used = folders.get(subdomain, empty)
        limit = plans.get_plan(plan).storage_bytes
        stores.append({
            "folder": subdomain, "tenant_id": str(tenant_id), "name": name, "plan": plan,
            **used, "limit_bytes": limit, "pct": round(used["bytes"] / limit * 100, 1) if limit else 0.0,
        })
    stores.sort(key=lambda s: (-s["bytes"], s["name"]))
    known = {s[0] for s in sites}
    others = []
    for folder in sorted((set(folders) | set(top_folders)) - known):
        used = folders.get(folder, empty)
        platform = folder in storage_report.PLATFORM_FOLDERS or folder == ""
        others.append({
            "folder": folder or "(files in the root)",
            "kind": "platform" if platform else "unmatched",
            "label": storage_report.PLATFORM_FOLDERS.get(folder, "Files in the root" if not folder else "No store with this name"),
            "bytes": used["bytes"], "files": used["files"],
        })
    others.sort(key=lambda o: -o["bytes"])
    return stores, others


@router.get("/storage", response_model=StorageOut)
async def storage(admin: SuperAdminUser, db: DB, refresh: bool = False) -> dict:
    report, error = await storage_report.media_report(refresh)
    sites = (await db.execute(
        select(Site.subdomain, Site.tenant_id, Site.name, Tenant.plan).join(Tenant, Tenant.id == Site.tenant_id)
    )).all()
    folders = report["folders"] if report else {}
    stores, others = split_folders(folders, report["top_folders"] if report else [], [tuple(s) for s in sites])
    kinds = report["kinds"] if report else {}
    count = lambda k, field: kinds.get(k, {}).get(field, 0)  # noqa: E731
    totals = {
        "files": sum(k["files"] for k in kinds.values()),
        "bytes": sum(k["bytes"] for k in kinds.values()),
        "images": count("image", "files"), "image_bytes": count("image", "bytes"),
        "videos": count("video", "files"), "video_bytes": count("video", "bytes"),
        "raw": count("raw", "files"), "raw_bytes": count("raw", "bytes"),
        "top_folders": len(report["top_folders"]) if report else 0,
        "all_folders": report["folder_paths"] if report else 0,
        "stores": len(stores),
        "stores_near_limit": sum(1 for s in stores if s["pct"] >= NEAR * 100),
        "unmatched_folders": sum(1 for o in others if o["kind"] == "unmatched"),
        "unmatched_bytes": sum(o["bytes"] for o in others if o["kind"] == "unmatched"),
    }
    return {
        "scanned_at": report["scanned_at"] if report else None,
        "partial": bool(report and report["partial"]),
        "error": error,
        "account": report["account"] if report else None,
        "totals": totals,
        "stores": stores if report else [],
        "other_folders": others,
        "largest": report["largest"] if report else [],
        "database": await storage_report.database_report(db),
        "server": await storage_report.server_report(),
    }
