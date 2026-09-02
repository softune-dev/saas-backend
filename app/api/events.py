"""Events — merchant sale/promo campaigns.

Split out from commerce.py because this resource owns its own many-to-many
join table (event_products) and its own conflict-check business rule (a
product may belong to only one ACTIVE event at a time — see
migrations/055_events.sql's own comment), not just flat CRUD.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, events as events_mod, media
from app.db import get_db
from app.models import Event, Product, Site, Tenant
from app.schemas import EventCreate, EventOut, EventUpdate, Page
from app.security import CurrentUser

router = APIRouter(tags=["events"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


def _event_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        site_id=event.site_id,
        name=event.name,
        slug=event.slug,
        description=event.description,
        image_url=event.image_url,
        cta_label=event.cta_label,
        discount_percent=event.discount_percent,
        is_active=event.is_active,
        product_ids=[p.id for p in event.products],
        product_count=len(event.products),
        created_at=event.created_at,
    )


async def _resolve_products(
    db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> list[Product]:
    if not product_ids:
        return []
    rows = list(
        (
            await db.execute(
                select(Product).where(Product.id.in_(product_ids), Product.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    found = {p.id for p in rows}
    missing = [str(pid) for pid in product_ids if pid not in found]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Products not found: {', '.join(missing)}")
    cross_site = [p.name for p in rows if p.site_id != site_id]
    if cross_site:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product is on another site")
    return rows


async def _ensure_no_active_conflict(
    db: AsyncSession,
    site_id: uuid.UUID,
    products: list[Product],
    exclude_event_id: uuid.UUID | None,
) -> None:
    """A product may belong to only one ACTIVE event at a time — app-layer
    only, not a DB constraint (draft/inactive events must stay free to
    list any products without conflict)."""
    if not products:
        return
    product_ids = [p.id for p in products]
    stmt = (
        select(Event)
        .join(Event.products)
        .where(Event.site_id == site_id, Event.is_active, Product.id.in_(product_ids))
    )
    if exclude_event_id is not None:
        stmt = stmt.where(Event.id != exclude_event_id)
    conflicting = list((await db.execute(stmt)).scalars().unique().all())
    if conflicting:
        conflict_names = sorted(
            {p.name for e in conflicting for p in e.products if p.id in product_ids}
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Already in another active event: " + ", ".join(conflict_names),
        )


@router.get("/sites/{site_id}/events", response_model=Page[EventOut])
async def list_events(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)
    rows, total = await crud.list_scoped(
        db,
        Event,
        user.tenant_id,
        filters=[Event.site_id == site_id],
        order_by=Event.created_at.desc(),
        limit=limit,
        offset=offset,
    )
    return {"items": [_event_out(e) for e in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/sites/{site_id}/events/{event_id}", response_model=EventOut)
async def get_event(site_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser, db: DB) -> EventOut:
    await _owned_site(db, user.tenant_id, site_id)
    event = await crud.get_scoped(db, Event, user.tenant_id, event_id)
    return _event_out(event)


@router.post("/sites/{site_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_event(
    site_id: uuid.UUID, payload: EventCreate, user: CurrentUser, db: DB
) -> EventOut:
    site = await _owned_site(db, user.tenant_id, site_id)
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    existing_count = await crud.count_scoped(db, Event, user.tenant_id)
    events_mod.ensure_within_event_limit(existing_count, tenant.plan)

    products = await _resolve_products(db, user.tenant_id, site_id, payload.product_ids)
    if payload.is_active:
        await _ensure_no_active_conflict(db, site_id, products, exclude_event_id=None)

    data = payload.model_dump(exclude={"product_ids"})
    data["slug"] = payload.slug or crud.slugify(payload.name, "event")
    event = Event(site_id=site.id, tenant_id=site.tenant_id, products=products, **data)
    event = await crud.save(db, event)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return _event_out(event)


@router.patch("/sites/{site_id}/events/{event_id}", response_model=EventOut)
async def update_event(
    site_id: uuid.UUID, event_id: uuid.UUID, payload: EventUpdate, user: CurrentUser, db: DB
) -> EventOut:
    site = await _owned_site(db, user.tenant_id, site_id)
    event = await crud.get_scoped(db, Event, user.tenant_id, event_id)

    new_products = event.products
    if payload.product_ids is not None:
        new_products = await _resolve_products(db, user.tenant_id, site_id, payload.product_ids)

    will_be_active = payload.is_active if payload.is_active is not None else event.is_active
    if will_be_active:
        await _ensure_no_active_conflict(db, site_id, new_products, exclude_event_id=event.id)

    old_image_url = event.image_url
    data = payload.model_dump(exclude_unset=True, exclude={"product_ids"})
    crud.apply_updates(event, data)
    if payload.product_ids is not None:
        event.products = new_products
    event = await crud.save(db, event)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    if old_image_url and old_image_url != event.image_url:
        media.delete_by_url(old_image_url, site.subdomain)
    return _event_out(event)


@router.delete("/sites/{site_id}/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(site_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    event = await crud.get_scoped(db, Event, user.tenant_id, event_id)
    image_url = event.image_url
    # Products bound to this event are NOT deleted — only the event_products
    # membership rows cascade (ON DELETE CASCADE is on event_id, not on the
    # products table itself).
    await crud.delete(db, event)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    if image_url:
        media.delete_by_url(image_url, site.subdomain)
