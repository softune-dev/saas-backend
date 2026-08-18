"""Page editing — where the block-based editor actually writes.

Also exposes GET /blocks, the registry the admin panel builds its forms from.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import blocks as block_registry
from app import cache, crud, queue
from app.db import get_db
from app.models import Site, SitePage
from app.schemas import PageCreate, PageOut, PageUpdate
from app.security import CurrentUser

router = APIRouter(tags=["pages"])
DB = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
#  Block registry — static, no auth, no database. The admin panel's form spec.
# ---------------------------------------------------------------------------


@router.get("/blocks")
async def list_blocks() -> dict:
    """Every editable block type and its fields.

    The admin panel calls this ONCE on load and renders every edit form from the
    response. That is why adding a block type needs no admin-panel deploy: the
    form is generated, not written.
    """
    return {"blocks": block_registry.catalogue()}


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    """Confirm the site belongs to the caller before touching anything under it.

    Every nested route calls this first. Without it, `POST /sites/<someone
    else's id>/pages` would happily write into another tenant's site — the
    classic nested-resource authorisation hole.
    """
    return await crud.get_scoped(db, Site, tenant_id, site_id)


def _validate(raw_blocks: list[dict]) -> list[dict]:
    """Run block data through the registry, converting failures into a clean 422."""
    try:
        return block_registry.validate_blocks(raw_blocks)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/sites/{site_id}/pages", response_model=list[PageOut])
async def list_pages(site_id: uuid.UUID, user: CurrentUser, db: DB) -> list[SitePage]:
    await _owned_site(db, user.tenant_id, site_id)
    stmt = (
        select(SitePage)
        .where(SitePage.site_id == site_id)
        .order_by(SitePage.sort_order, SitePage.slug)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/sites/{site_id}/pages", response_model=PageOut, status_code=status.HTTP_201_CREATED
)
async def create_page(
    site_id: uuid.UUID, payload: PageCreate, user: CurrentUser, db: DB
) -> SitePage:
    site = await _owned_site(db, user.tenant_id, site_id)

    page = SitePage(
        site_id=site.id,
        # Copied from the verified site, never from client input — this is what
        # makes the denormalised tenant_id trustworthy.
        tenant_id=site.tenant_id,
        slug=payload.slug,
        title=payload.title,
        blocks=_validate(payload.blocks),
        seo=payload.seo,
        sort_order=payload.sort_order,
    )
    page = await crud.save(db, page)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    return page


@router.get("/sites/{site_id}/pages/{page_id}", response_model=PageOut)
async def get_page(
    site_id: uuid.UUID, page_id: uuid.UUID, user: CurrentUser, db: DB
) -> SitePage:
    await _owned_site(db, user.tenant_id, site_id)
    page = await crud.get_scoped(db, SitePage, user.tenant_id, page_id)
    # Belt-and-braces: the page is tenant-scoped already, but this also rejects a
    # page id from a DIFFERENT site of the same tenant, so the URL cannot lie.
    if page.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return page


@router.patch("/sites/{site_id}/pages/{page_id}", response_model=PageOut)
async def update_page(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    payload: PageUpdate,
    user: CurrentUser,
    db: DB,
) -> SitePage:
    """The endpoint the editor's Save button hits."""
    site = await _owned_site(db, user.tenant_id, site_id)
    page = await crud.get_scoped(db, SitePage, user.tenant_id, page_id)
    if page.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")

    data = payload.model_dump(exclude_unset=True)
    if "blocks" in data:
        data["blocks"] = _validate(data["blocks"])

    crud.apply_updates(page, data)
    page = await crud.save(db, page)

    # Cache first (so a reload shows the change immediately), then queue the
    # Vercel refresh (so the live site catches up a moment later).
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    if site.status == "published":
        await queue.publish(
            queue.JOB_REVALIDATE_SITE,
            {"site_id": str(site.id), "paths": [f"/{page.slug}".rstrip("/") or "/"]},
        )
    return page


@router.delete(
    "/sites/{site_id}/pages/{page_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_page(
    site_id: uuid.UUID, page_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    page = await crud.get_scoped(db, SitePage, user.tenant_id, page_id)
    if page.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    await crud.delete(db, page)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
