"""Sites and templates — buy a template, get an editable site."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, media, notifications, queue, vercel
from app.db import get_db
from app.models import Site, SitePage, Template
from app.schemas import (
    DomainStatusOut,
    Page,
    SiteCreate,
    SiteOut,
    SiteUpdate,
    TemplateOut,
)
from app.security import CurrentUser

router = APIRouter(tags=["sites"])
DB = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
#  Templates — the catalogue. Shared, not tenant-owned, so no scoping needed.
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(db: DB) -> list[Template]:
    stmt = select(Template).where(Template.is_active).order_by(Template.name)
    async def _fetch():
        return list((await db.execute(stmt)).scalars().all())
    return await crud._retry_on_pooler_error(_fetch)


# ---------------------------------------------------------------------------
#  Sites
# ---------------------------------------------------------------------------


@router.get("/sites", response_model=Page[SiteOut])
async def list_sites(
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows, total = await crud.list_scoped(
        db, Site, user.tenant_id,
        order_by=Site.created_at.desc(), limit=limit, offset=offset,
    )
    # A plain dict, not a Page(...) instance: FastAPI validates it against the
    # declared response_model Page[SiteOut], which converts the ORM rows via
    # from_attributes. Returning an unparameterised Page would make Pydantic
    # reject it as the wrong model class.
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post("/sites", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
async def create_site(payload: SiteCreate, user: CurrentUser, db: DB) -> Site:
    """Provision a site from a template.

    The template's default_theme and default_pages are COPIED in, not referenced.
    That matters: the customer must be able to edit their content freely, and a
    later change to the template must never silently rewrite live customer sites.
    """
    template = (
        await db.execute(
            select(Template).where(
                Template.id == payload.template_id, Template.is_active
            )
        )
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found or inactive")

    site = await crud.provision_site(
        db,
        tenant_id=user.tenant_id,
        template=template,
        name=payload.name,
        subdomain=payload.subdomain,
    )
    await db.commit()
    await db.refresh(site)
    return site


@router.get("/sites/{site_id}", response_model=SiteOut)
async def get_site(site_id: uuid.UUID, user: CurrentUser, db: DB) -> Site:
    return await crud.get_scoped(db, Site, user.tenant_id, site_id)


@router.get("/sites/{site_id}/domain-status", response_model=DomainStatusOut)
async def get_domain_status(site_id: uuid.UUID, user: CurrentUser, db: DB) -> DomainStatusOut:
    """Real check against Vercel — is site.custom_domain actually attached
    to THIS site's project AND correctly pointed at Vercel? See
    vercel.check_domain_connected for why both have to be checked.

    Separate endpoint, not folded into SiteOut, because it's a live network
    call to Vercel on every request — fine on-demand when the merchant is
    looking at their Domains settings, wrong to pay on every site fetch.
    """
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    if not site.custom_domain:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No custom domain set on this site.")
    connected = await vercel.check_domain_connected(
        site.custom_domain, site.template.vercel_project_id or ""
    )
    return DomainStatusOut(domain=site.custom_domain, connected=connected)


@router.patch("/sites/{site_id}", response_model=SiteOut)
async def update_site(
    site_id: uuid.UUID, payload: SiteUpdate, user: CurrentUser, db: DB
) -> Site:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    old_domain = site.custom_domain
    old_theme = site.theme or {}

    crud.apply_updates(site, payload.model_dump(exclude_unset=True))
    site = await crud.save(db, site)

    # Any change to theme/business/seo changes what visitors see, so the cached
    # config must go. Drop the OLD hostname too — otherwise a stale entry keeps
    # serving under a domain the site no longer uses.
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    if old_domain and old_domain != site.custom_domain:
        await cache.drop(cache.site_key(old_domain))

    # Cloudinary has no idea a URL stopped being referenced the moment this
    # save overwrote theme JSON with a new logo/hero/testimonial image list —
    # clean up anything the old theme pointed at that the new one doesn't,
    # best-effort, after the save itself has already succeeded.
    if "theme" in payload.model_fields_set:
        dropped = media.theme_image_urls(old_theme) - media.theme_image_urls(site.theme or {})
        # media.delete_by_url is a synchronous Cloudinary SDK call — awaiting
        # it directly blocks the event loop (and every other concurrent
        # request on this process, since uvicorn runs a single worker) for
        # the full round trip, times however many images the theme edit
        # dropped. Threadpool moves that blocking off the loop; still
        # best-effort (delete_by_url already swallows its own failures).
        for url in dropped:
            await run_in_threadpool(media.delete_by_url, url, site.subdomain)

    if site.status == "published":
        await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})
        if old_domain != site.custom_domain:
            # A custom domain (or removing one, falling back to the
            # subdomain) needs the same Vercel attach as publish_site does —
            # see queue.JOB_ATTACH_DOMAIN and app/vercel.py. Only meaningful
            # once the site is actually live; an unpublished site has
            # nothing for a domain to point at yet.
            await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site.id)})
    if old_domain and old_domain != site.custom_domain:
        # The OLD custom domain is now unused — detach it from Vercel too,
        # not just cleared from our own database, or it would silently
        # keep serving this site's storefront forever. Not gated to
        # "published": the domain may have been attached during an earlier
        # published state that's since changed, and detaching an
        # already-unattached domain is a harmless no-op anyway.
        await queue.publish(
            queue.JOB_DETACH_DOMAIN, {"site_id": str(site.id), "domain": old_domain}
        )
    return site


# Minimum gap between two publishes of the same site. Publishing kicks off a
# real Vercel build (1-3 min), CDN propagation, and a screenshot job that
# waits on both — publishing again mid-flight risks racing those against
# each other (e.g. the screenshot job capturing whichever build happened to
# finish last, not the one the merchant thinks they just published).
_PUBLISH_COOLDOWN_SECONDS = 60


@router.post("/sites/{site_id}/publish", response_model=SiteOut)
async def publish_site(site_id: uuid.UUID, user: CurrentUser, db: DB) -> Site:
    """Take a site live: publish every page, clear noindex, refresh caches."""
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    if site.published_at:
        elapsed = (datetime.now(UTC) - site.published_at).total_seconds()
        remaining = _PUBLISH_COOLDOWN_SECONDS - elapsed
        if remaining > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Please wait {int(remaining) + 1}s before publishing again.",
            )

    pages = (
        await db.execute(select(SitePage).where(SitePage.site_id == site.id))
    ).scalars().all()
    if not pages:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Add at least one page before publishing."
        )
    for page in pages:
        page.is_published = True

    site.status = "published"
    site.published_at = datetime.now(UTC)
    site.seo = {**(site.seo or {}), "noindex": False}
    await db.commit()
    await db.refresh(site)

    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await queue.publish(
        queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id), "paths": ["/"]}
    )
    await queue.publish(queue.JOB_GENERATE_SITEMAP, {"site_id": str(site.id)})
    # Idempotent (see app/vercel.py) — safe to fire on every publish, not
    # just the first one, so a re-publish also self-heals a domain that
    # somehow got detached.
    await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site.id)})
    # Refreshes the Themes page card's screenshot on every publish, not just
    # the first — the storefront looks different after every real edit.
    await queue.publish(queue.JOB_CAPTURE_SCREENSHOT, {"site_id": str(site.id)})
    await notifications.notify(
        db,
        tenant_id=site.tenant_id,
        site_id=site.id,
        type="site_published",
        title="Site published",
        body=f"{site.name} is now live.",
    )
    return site


@router.post("/sites/{site_id}/unpublish", response_model=SiteOut)
async def unpublish_site(site_id: uuid.UUID, user: CurrentUser, db: DB) -> Site:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    site.status = "draft"
    site.seo = {**(site.seo or {}), "noindex": True}
    await db.commit()
    await db.refresh(site)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await notifications.notify(
        db,
        tenant_id=site.tenant_id,
        site_id=site.id,
        type="site_unpublished",
        title="Site unpublished",
        body=f"{site.name} was taken offline.",
    )
    return site


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    subdomain, custom = site.subdomain, site.custom_domain
    # Pages, products, categories and orders all go with it via ON DELETE CASCADE
    # in the schema — one statement, no orphans left behind.
    await crud.delete(db, site)
    await cache.invalidate_site(subdomain, custom)
