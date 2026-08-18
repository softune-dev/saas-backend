"""Courier connections — a site's own Steadfast/Pathao/RedX merchant account.

Backs dashboard/lib/api/courier.ts exactly (that file is the contract this
was implemented against). Only Steadfast has a live connect flow; Pathao/RedX
are reserved routes for a later wave, matching the frontend's "Coming soon"
cards.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import courier_crypto, crud, steadfast
from app.db import get_db
from app.models import CourierConnection, Site
from app.schemas import CourierConnectionOut, SteadfastConnectIn
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/couriers", tags=["courier"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("", response_model=list[CourierConnectionOut])
async def list_couriers(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[CourierConnection]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, CourierConnection, user.tenant_id,
        filters=[CourierConnection.site_id == site_id],
        order_by=CourierConnection.created_at.desc(),
        limit=10,  # at most 3 providers exist; 10 is headroom, not a real cap
    )
    return rows


@router.post(
    "/steadfast",
    response_model=CourierConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def connect_steadfast(
    site_id: uuid.UUID, payload: SteadfastConnectIn, user: CurrentUser, db: DB
) -> CourierConnection:
    site = await _owned_site(db, user.tenant_id, site_id)

    ok, error = await steadfast.verify_credentials(
        payload.api_key, payload.secret_key, payload.base_url
    )
    # Save the credentials either way — a merchant who fat-fingers their key
    # should see it listed as "error" and be able to fix it, not lose the
    # attempt entirely. (Cache/queue failures swallow per CLAUDE.md rule 9;
    # this is the same instinct applied to a third-party verification call.)
    connection = CourierConnection(
        tenant_id=site.tenant_id,
        site_id=site.id,
        provider="steadfast",
        status="connected" if ok else "error",
        label=payload.label,
        api_key_encrypted=courier_crypto.encrypt(payload.api_key),
        secret_key_encrypted=courier_crypto.encrypt(payload.secret_key),
        api_key_hint=courier_crypto.mask(payload.api_key),
        base_url=payload.base_url,
        last_verified_at=datetime.now(UTC) if ok else None,
    )
    connection = await crud.save(db, connection)

    if not ok:
        # 201 either way — the row was created; error is visible via `status`
        # and surfaced to the merchant, not raised as a failed request. A
        # bad key is an expected user outcome (see steadfast.py), not a 4xx.
        pass
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_courier(
    site_id: uuid.UUID, connection_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    connection = await crud.get_scoped(db, CourierConnection, user.tenant_id, connection_id)
    if connection.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CourierConnection not found")
    await crud.delete(db, connection)


@router.post("/{connection_id}/verify", response_model=CourierConnectionOut)
async def verify_courier_connection(
    site_id: uuid.UUID, connection_id: uuid.UUID, user: CurrentUser, db: DB
) -> CourierConnection:
    """Re-check stored credentials against the provider. Not called by the
    UI yet (dashboard/lib/api/courier.ts marks it optional) — here so a
    merchant can eventually confirm a connection still works without
    re-entering credentials."""
    await _owned_site(db, user.tenant_id, site_id)
    connection = await crud.get_scoped(db, CourierConnection, user.tenant_id, connection_id)
    if connection.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CourierConnection not found")

    if connection.provider != "steadfast":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Verification isn't implemented for {connection.provider} yet.",
        )

    api_key = courier_crypto.decrypt(connection.api_key_encrypted)
    secret_key = courier_crypto.decrypt(connection.secret_key_encrypted)
    ok, _error = await steadfast.verify_credentials(api_key, secret_key, connection.base_url)

    connection.status = "connected" if ok else "error"
    if ok:
        connection.last_verified_at = datetime.now(UTC)
    return await crud.save(db, connection)
