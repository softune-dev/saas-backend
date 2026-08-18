"""Payment method connections — how a storefront's checkout gets paid.

Backs dashboard/lib/api/payments.ts and the /payments dashboard page (built
UI-first against dashboard/components/payments/payment-data.ts's
PAYMENT_CATALOG). Six providers, three shapes:

  - cod: no credentials, just config (optional fee note).
  - manual: no credentials — customer pays the merchant's own bKash/Nagad/
    Rocket number directly and submits a transaction ID/screenshot for the
    merchant to verify by hand. config holds the number + accepted wallets.
  - bkash / nagad / sslcommerz / rocket: real gateway credentials, Fernet-
    encrypted at rest (reusing app/courier_crypto.py's key — same trust
    boundary as courier credentials, no reason to manage a second key for
    the same sensitivity level). We have no live merchant accounts for any
    of these yet, so connecting one here stores credentials for later, no
    verification call exists (unlike Steadfast's connect flow).

A `status='connected'` row here is what makes a provider show up at real
checkout — see app/api/public.py's get_site_config, which lists connected
providers (non-secret fields only) as `site.payment_methods`, and the
storefront's checkout page, which only offers whichever of those it knows
how to render (currently cod/manual — gateways have no live checkout flow
yet regardless of connection status). Manual payment VERIFICATION (matching
a submitted transaction ID/screenshot to an order) is still a separate,
unbuilt piece of work.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, courier_crypto, crud
from app.db import get_db
from app.models import PaymentConnection, Site
from app.schemas import PaymentConnectIn, PaymentConnectionOut
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/payments", tags=["payments"])
DB = Annotated[AsyncSession, Depends(get_db)]

_PROVIDERS = {"cod", "manual", "bkash", "nagad", "sslcommerz", "rocket"}
_GATEWAY_PROVIDERS = {"bkash", "nagad", "sslcommerz", "rocket"}


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("", response_model=list[PaymentConnectionOut])
async def list_payments(site_id: uuid.UUID, user: CurrentUser, db: DB) -> list[PaymentConnection]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, PaymentConnection, user.tenant_id,
        filters=[PaymentConnection.site_id == site_id],
        order_by=PaymentConnection.created_at.desc(),
        limit=10,  # six providers exist total; 10 is headroom, not a real cap
    )
    return rows


@router.post(
    "/{provider}",
    response_model=PaymentConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def connect_payment(
    site_id: uuid.UUID,
    provider: str,
    payload: PaymentConnectIn,
    user: CurrentUser,
    db: DB,
) -> PaymentConnection:
    """Create or update this site's connection for one provider.

    Upsert, not insert-only: (site_id, provider) is unique, and reconnecting
    (e.g. changing the manual payment number, or rotating a gateway key)
    should update the existing row rather than 409ing.
    """
    if provider not in _PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown payment provider '{provider}'")

    site = await _owned_site(db, user.tenant_id, site_id)

    if provider in _GATEWAY_PROVIDERS:
        if not payload.api_key or not payload.secret_key:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{provider} needs both an API key and a secret key.",
            )
        config: dict = {}
        if payload.merchant_id:
            config["merchant_id"] = payload.merchant_id
        api_key_encrypted = courier_crypto.encrypt(payload.api_key)
        secret_key_encrypted = courier_crypto.encrypt(payload.secret_key)
        api_key_hint = courier_crypto.mask(payload.api_key)
    elif provider == "manual":
        if not payload.payment_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Manual payment needs the number customers should pay to.",
            )
        config = {
            "payment_number": payload.payment_number,
            "wallets": payload.wallets or [],
        }
        api_key_encrypted = secret_key_encrypted = api_key_hint = None
    else:  # cod
        config = {}
        if payload.cod_fee_cents is not None:
            config["cod_fee_cents"] = payload.cod_fee_cents
        api_key_encrypted = secret_key_encrypted = api_key_hint = None

    existing = (
        await db.execute(
            select(PaymentConnection).where(
                PaymentConnection.site_id == site_id,
                PaymentConnection.provider == provider,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.label = payload.label
        existing.config = config
        existing.status = "connected"
        if provider in _GATEWAY_PROVIDERS:
            existing.api_key_encrypted = api_key_encrypted
            existing.secret_key_encrypted = secret_key_encrypted
            existing.api_key_hint = api_key_hint
        connection = existing
    else:
        connection = PaymentConnection(
            tenant_id=site.tenant_id,
            site_id=site.id,
            provider=provider,
            status="connected",
            label=payload.label,
            config=config,
            api_key_encrypted=api_key_encrypted,
            secret_key_encrypted=secret_key_encrypted,
            api_key_hint=api_key_hint,
        )

    connection = await crud.save(db, connection)
    # get_site_config caches site.payment_methods in Redis — without this, a
    # merchant enabling COD wouldn't see it at checkout until something else
    # happened to invalidate the cache.
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_payment(
    site_id: uuid.UUID, connection_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    connection = await crud.get_scoped(db, PaymentConnection, user.tenant_id, connection_id)
    if connection.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PaymentConnection not found")
    await crud.delete(db, connection)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
