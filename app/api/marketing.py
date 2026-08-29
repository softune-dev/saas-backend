"""Marketing/tracking integrations — currently just Meta Conversions API.

Backs dashboard/lib/api/marketing.ts and the SEO/Marketing settings page.
One provider so far: `meta_capi`. No verification call exists (same as
payments.py's gateway providers) — Meta has no simple "check this token"
endpoint short of actually sending an event, so connecting one here stores
the token for the worker to use on the next real order, same trust model
as an unverified payment gateway credential.

Client-side pixel IDs (Meta Pixel, TikTok Pixel, GTM container) are NOT
handled here — those aren't secrets, they live in Site.seo and go through
the existing generic PATCH /sites/{id} endpoint (see app/api/sites.py).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import courier_crypto, crud
from app.db import get_db
from app.models import MarketingConnection, Site
from app.schemas import MarketingConnectionOut, MetaCapiConnectIn
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/marketing", tags=["marketing"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("", response_model=list[MarketingConnectionOut])
async def list_marketing_connections(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[MarketingConnection]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, MarketingConnection, user.tenant_id,
        filters=[MarketingConnection.site_id == site_id],
        order_by=MarketingConnection.created_at.desc(),
        limit=10,  # one provider exists today; 10 is headroom, not a real cap
    )
    return rows


@router.post(
    "/meta-capi",
    response_model=MarketingConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def connect_meta_capi(
    site_id: uuid.UUID, payload: MetaCapiConnectIn, user: CurrentUser, db: DB
) -> MarketingConnection:
    site = await _owned_site(db, user.tenant_id, site_id)

    rows, _ = await crud.list_scoped(
        db, MarketingConnection, user.tenant_id,
        filters=[MarketingConnection.site_id == site.id, MarketingConnection.provider == "meta_capi"],
        limit=1,
    )
    existing = rows[0] if rows else None
    token_encrypted = courier_crypto.encrypt(payload.access_token)
    token_hint = courier_crypto.mask(payload.access_token)

    if existing is not None:
        existing.access_token_encrypted = token_encrypted
        existing.access_token_hint = token_hint
        existing.status = "connected"
        connection = await crud.save(db, existing)
    else:
        connection = await crud.save(
            db,
            MarketingConnection(
                tenant_id=site.tenant_id,
                site_id=site.id,
                provider="meta_capi",
                status="connected",
                access_token_encrypted=token_encrypted,
                access_token_hint=token_hint,
            ),
        )
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_marketing(
    site_id: uuid.UUID, connection_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    connection = await crud.get_scoped(db, MarketingConnection, user.tenant_id, connection_id)
    if connection.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MarketingConnection not found")
    await crud.delete(db, connection)
