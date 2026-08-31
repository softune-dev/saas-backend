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
    the same sensitivity level). bkash gets a real live credential check
    (see app/bkash.py — its grant-token call doubles as one, side-effect
    free); sslcommerz and nagad are saved unverified, same reasoning as
    eCourier's courier connect (no standalone "check these creds" endpoint
    exists for either without also creating a real transaction context);
    rocket has no known public merchant API at all — see this file's own
    provider branch below.

A `status='connected'` row here is what makes a provider show up at real
checkout — see app/api/public.py's get_site_config, which lists connected
providers (non-secret fields only) as `site.payment_methods`, and the
storefront's checkout page, which only offers whichever of those it knows
how to render (currently cod/manual — gateway CHECKOUT is still a separate,
larger unbuilt piece: creating a payment session, redirecting the customer,
and handling the success/fail callback, same scope boundary as
app/steadfast.py not creating shipments yet). Manual payment VERIFICATION
(matching a submitted transaction ID/screenshot to an order) is also still
separate, unbuilt work.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bkash, cache, courier_crypto, crud
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

    verified_ok: bool | None = None  # None = no live check for this provider
    extra_encrypted: str | None = None

    if provider == "bkash":
        if not (payload.api_key and payload.secret_key and payload.username and payload.password):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "bKash needs app key, app secret, username, and password.",
            )
        verified_ok, _error = await bkash.verify_credentials(
            payload.api_key, payload.secret_key, payload.username, payload.password,
            payload.sandbox,
        )
        config = {"sandbox": payload.sandbox}
        api_key_encrypted = courier_crypto.encrypt(payload.api_key)
        secret_key_encrypted = courier_crypto.encrypt(payload.secret_key)
        extra_encrypted = courier_crypto.encrypt(
            json.dumps({"username": payload.username, "password": payload.password})
        )
        api_key_hint = courier_crypto.mask(payload.api_key)

    elif provider == "sslcommerz":
        if not (payload.api_key and payload.secret_key):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "SSLCommerz needs a store ID and store password."
            )
        # No live check: SSLCommerz has no standalone "validate credentials"
        # endpoint — their Session API always creates a real (if unpaid)
        # session as a side effect, so this is saved unverified, same as
        # eCourier's connect_ecourier and for the same reason.
        config = {"sandbox": payload.sandbox}
        api_key_encrypted = courier_crypto.encrypt(payload.api_key)
        secret_key_encrypted = courier_crypto.encrypt(payload.secret_key)
        api_key_hint = courier_crypto.mask(payload.api_key)

    elif provider == "nagad":
        if not (payload.merchant_id and payload.merchant_private_key and payload.nagad_public_key):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Nagad needs a merchant ID, your RSA private key, and Nagad's public key.",
            )
        # No live check: every real Nagad API call is signed per-transaction
        # with these keys, and there's no standalone "verify" endpoint that
        # doesn't also require simulating a transaction context. Saved
        # unverified, same reasoning as SSLCommerz above.
        config = {"merchant_id": payload.merchant_id, "sandbox": payload.sandbox}
        api_key_encrypted = None
        secret_key_encrypted = courier_crypto.encrypt(payload.merchant_private_key)
        extra_encrypted = courier_crypto.encrypt(
            json.dumps({"nagad_public_key": payload.nagad_public_key})
        )
        api_key_hint = courier_crypto.mask(payload.merchant_id)

    elif provider in _GATEWAY_PROVIDERS:  # rocket — no known public API, see app/api/payments.py's module docstring
        if not payload.api_key or not payload.secret_key:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{provider} needs both an API key and a secret key.",
            )
        config = {}
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

    # Only a real, live check (bkash) can ever produce "error" — providers
    # with no verification step are marked "connected" as entered, same
    # save-either-way reasoning as connect_steadfast in app/api/courier.py.
    conn_status = "connected" if verified_ok in (None, True) else "error"
    last_verified_at = datetime.now(UTC) if verified_ok is True else None

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
        existing.status = conn_status
        existing.last_verified_at = last_verified_at
        if provider in _GATEWAY_PROVIDERS:
            existing.api_key_encrypted = api_key_encrypted
            existing.secret_key_encrypted = secret_key_encrypted
            existing.extra_encrypted = extra_encrypted
            existing.api_key_hint = api_key_hint
        connection = existing
    else:
        connection = PaymentConnection(
            tenant_id=site.tenant_id,
            site_id=site.id,
            provider=provider,
            status=conn_status,
            label=payload.label,
            config=config,
            api_key_encrypted=api_key_encrypted,
            secret_key_encrypted=secret_key_encrypted,
            extra_encrypted=extra_encrypted,
            api_key_hint=api_key_hint,
            last_verified_at=last_verified_at,
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
