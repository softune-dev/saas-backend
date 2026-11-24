"""Free-plan rules for writing sites and pages: SEO, fraud rules, custom
domain and the number of stores.

These are ROUTE dependencies on purpose. app/api/ai_setup.py calls
sites.update_site() itself to save the description written at sign-up, and
that server-side write has to keep working on Free; only what a merchant sends
over HTTP is checked.

The dashboard saves whole objects, so a locked field is refused only when it
really changes. One that repeats what is stored, or says nothing, is dropped
from the payload instead: left in, it would overwrite the column, and an empty
`seo` would wipe its noindex.
"""

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, plan_gate, plans
from app.config import settings
from app.db import get_db
from app.models import Site, SitePage, Tenant
from app.schemas import PageCreate, PageUpdate, SiteUpdate
from app.security import CurrentUser

DB = Annotated[AsyncSession, Depends(get_db)]

_EMPTY: tuple[Any, ...] = (None, "", [], {})
# Set by publish and unpublish, never by the SEO form.
_NOT_MERCHANT_SEO = {"noindex"}


def _changes(sent: dict | None, stored: dict | None) -> bool:
    stored = stored or {}
    return any(
        value not in _EMPTY and stored.get(key) != value
        for key, value in (sent or {}).items()
        if key not in _NOT_MERCHANT_SEO
    )


async def allowed_site_update(
    site_id: uuid.UUID, payload: SiteUpdate, user: CurrentUser, db: DB
) -> SiteUpdate:
    fields = (("seo", "seo"), ("fraud_rules", "fraud"), ("custom_domain", "domain"))
    sent_fields = [pair for pair in fields if pair[0] in payload.model_fields_set]
    if not sent_fields:
        return payload
    plan = await plan_gate.tenant_plan(db, user.tenant_id)
    locked = [pair for pair in sent_fields if not plans.has_feature(plan, pair[1])]
    if not locked:
        return payload
    # Ownership first: somebody else's site is a 404, never a plan message.
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    drop: set[str] = set()
    for field, feature in locked:
        sent = getattr(payload, field)
        if field == "custom_domain":
            # Removing a domain, or repeating the one in use, stays possible.
            if sent not in (None, site.custom_domain, site.pending_custom_domain):
                plans.ensure_feature(plan, feature)
            continue
        if _changes(sent, getattr(site, field)):
            plans.ensure_feature(plan, feature)
        drop.add(field)
    return SiteUpdate.model_validate(payload.model_dump(exclude_unset=True, exclude=drop))


async def allowed_page_create(
    site_id: uuid.UUID, payload: PageCreate, user: CurrentUser, db: DB
) -> PageCreate:
    if _changes(payload.seo, {}):
        plan = await plan_gate.tenant_plan(db, user.tenant_id)
        if not plans.has_feature(plan, "seo"):
            await crud.get_scoped(db, Site, user.tenant_id, site_id)
            plans.ensure_feature(plan, "seo")
    return payload


async def allowed_page_update(
    page_id: uuid.UUID, payload: PageUpdate, user: CurrentUser, db: DB
) -> PageUpdate:
    if "seo" not in payload.model_fields_set:
        return payload
    plan = await plan_gate.tenant_plan(db, user.tenant_id)
    if plans.has_feature(plan, "seo"):
        return payload
    page = await crud.get_scoped(db, SitePage, user.tenant_id, page_id)
    if _changes(payload.seo, page.seo):
        plans.ensure_feature(plan, "seo")
    return PageUpdate.model_validate(payload.model_dump(exclude_unset=True, exclude={"seo"}))


AllowedSiteUpdate = Annotated[SiteUpdate, Depends(allowed_site_update)]
AllowedPageCreate = Annotated[PageCreate, Depends(allowed_page_create)]
AllowedPageUpdate = Annotated[PageUpdate, Depends(allowed_page_update)]


async def _lock_plan(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """The plan, with the tenant row locked until this transaction ends so two
    requests cannot both take (or both delete) the last store. NO KEY UPDATE
    for the reason given in app/plan_limits.py."""
    return (
        await db.execute(
            select(Tenant.plan).where(Tenant.id == tenant_id).with_for_update(key_share=True)
        )
    ).scalar_one()


async def ensure_store_room(db: AsyncSession, tenant_id: uuid.UUID, subdomain: str) -> None:
    """Free is always limited. Other plans only once FREE_PLAN_ENABLED is on:
    today they have no limit, and some may already own more than the table."""
    plan = await plan_gate.tenant_plan(db, tenant_id)
    if plan != "free" and not settings.free_plan_enabled:
        return
    stores = plans.get_plan(await _lock_plan(db, tenant_id)).stores
    if await crud.count_scoped(db, Site, tenant_id) < stores:
        return
    # A taken address is the more useful answer, as it was before the limit.
    if (await db.execute(select(Site.id).where(Site.subdomain == subdomain))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "That subdomain is taken. Try another.")
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Store limit reached ({stores} on your current plan). Upgrade to add another store.",
    )


async def ensure_may_delete_store(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Free keeps its only store: deleting and recreating it would be a free
    theme switch."""
    if await plan_gate.tenant_plan(db, tenant_id) != "free":
        return
    if await _lock_plan(db, tenant_id) == "free" and await crud.count_scoped(db, Site, tenant_id) <= 1:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You cannot delete your only store on the Free plan.",
        )
