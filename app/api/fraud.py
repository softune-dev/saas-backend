"""Fraud protection — Settings -> Fraud Protection (dashboard/components/fraud/).

Small-business tier fraud model, three pieces:

- Phone blocklist + IP blocklist: merchant-maintained exact-match lists
  (deliberately NOT a computed risk score — a new site has no order history
  to score a customer against, so the only thing that works from day one is
  letting the merchant list numbers/IPs they already know are bad). Phone is
  checked at checkout only (app/api/public.py); IP is checked on EVERY
  /public/* request by app/main.py's ip_block middleware, so a blocked
  visitor can't browse the storefront at all.
- Device pending-lock + cooldown: hard blocks keyed on a client-generated
  device id (see templates/*/lib/device.ts), enforced in
  app/api/public.py's create_public_order. Both are rule toggles in
  sites.fraud_rules, not their own tables.
- Suspicious orders: soft-flags (hold_first_high_value / flag_burst_orders,
  see app/fraud.py's evaluate_soft_flags) that let the order through but
  mark it for merchant review — this module's list/review endpoints below.

Rule toggles themselves (enabled + threshold value for all of the above)
live in sites.fraud_rules JSONB, read/written through the existing generic
PATCH /sites/{id} (see app/api/sites.py's update_site), same as
shipping/faqs/legal. This module only owns the two blocklist tables and the
suspicious-orders review workflow.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud
from app.db import get_db
from app.models import FraudBlocklistEntry, FraudIpBlocklistEntry, Order, Site
from app.schemas import (
    FraudBlocklistEntryCreate,
    FraudBlocklistEntryOut,
    FraudIpBlocklistEntryCreate,
    FraudIpBlocklistEntryOut,
    FraudReviewIn,
    OrderOut,
)
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/fraud", tags=["fraud"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("/blocklist", response_model=list[FraudBlocklistEntryOut])
async def list_blocklist(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[FraudBlocklistEntry]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, FraudBlocklistEntry, user.tenant_id,
        filters=[FraudBlocklistEntry.site_id == site_id],
        order_by=FraudBlocklistEntry.created_at.desc(),
        limit=500,
    )
    return rows


@router.post("/blocklist", response_model=FraudBlocklistEntryOut, status_code=status.HTTP_201_CREATED)
async def add_to_blocklist(
    site_id: uuid.UUID, payload: FraudBlocklistEntryCreate, user: CurrentUser, db: DB
) -> FraudBlocklistEntry:
    site = await _owned_site(db, user.tenant_id, site_id)
    entry = FraudBlocklistEntry(
        tenant_id=site.tenant_id,
        site_id=site.id,
        phone=payload.phone.strip(),
        note=payload.note.strip(),
    )
    # save() maps the unique(site_id, phone) violation to a clean 409 (see
    # crud._explain) rather than a raw IntegrityError leaking to the client.
    return await crud.save(db, entry)


@router.delete("/blocklist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_blocklist(
    site_id: uuid.UUID, entry_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    entry = await crud.get_scoped(db, FraudBlocklistEntry, user.tenant_id, entry_id)
    if entry.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blocklist entry not found")
    await crud.delete(db, entry)


# =============================================================================
#  IP blocklist — see app/main.py's ip_block middleware for enforcement.
#  Exact-IP match only (FraudIpBlocklistEntryCreate rejects CIDR notation).
# =============================================================================


@router.get("/ip-blocklist", response_model=list[FraudIpBlocklistEntryOut])
async def list_ip_blocklist(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[FraudIpBlocklistEntry]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, FraudIpBlocklistEntry, user.tenant_id,
        filters=[FraudIpBlocklistEntry.site_id == site_id],
        order_by=FraudIpBlocklistEntry.created_at.desc(),
        limit=500,
    )
    return rows


@router.post(
    "/ip-blocklist", response_model=FraudIpBlocklistEntryOut, status_code=status.HTTP_201_CREATED
)
async def add_to_ip_blocklist(
    site_id: uuid.UUID, payload: FraudIpBlocklistEntryCreate, user: CurrentUser, db: DB
) -> FraudIpBlocklistEntry:
    site = await _owned_site(db, user.tenant_id, site_id)
    entry = FraudIpBlocklistEntry(
        tenant_id=site.tenant_id,
        site_id=site.id,
        ip_address=payload.ip_address,
        note=payload.note.strip(),
    )
    saved = await crud.save(db, entry)
    # The middleware checks every /public/* request against this list — a
    # merchant who just got attacked needs the block live immediately, not
    # after the cache's own short TTL happens to expire on its own.
    await cache.invalidate_ip_blocks(site.subdomain, site.custom_domain)
    return saved


@router.delete("/ip-blocklist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_ip_blocklist(
    site_id: uuid.UUID, entry_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    entry = await crud.get_scoped(db, FraudIpBlocklistEntry, user.tenant_id, entry_id)
    if entry.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IP blocklist entry not found")
    await crud.delete(db, entry)
    await cache.invalidate_ip_blocks(site.subdomain, site.custom_domain)


# =============================================================================
#  Suspicious orders — orders soft-flagged by app/fraud.py's
#  evaluate_soft_flags at checkout time (hold_first_high_value /
#  flag_burst_orders). The order was already created; this is a review
#  queue, not a block.
# =============================================================================


@router.get("/suspicious-orders", response_model=list[OrderOut])
async def list_suspicious_orders(site_id: uuid.UUID, user: CurrentUser, db: DB) -> list[Order]:
    await _owned_site(db, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, Order, user.tenant_id,
        filters=[Order.site_id == site_id, Order.fraud_status == "flagged"],
        order_by=Order.created_at.desc(),
        limit=200,
    )
    return rows


@router.post("/suspicious-orders/{order_id}/review", response_model=OrderOut)
async def review_suspicious_order(
    site_id: uuid.UUID, order_id: uuid.UUID, payload: FraudReviewIn, user: CurrentUser, db: DB
) -> Order:
    await _owned_site(db, user.tenant_id, site_id)
    order = await crud.get_scoped(db, Order, user.tenant_id, order_id)
    if order.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    # One-shot: don't let two merchant tabs race the same order into
    # different end states, and don't let a reviewed order be "reviewed"
    # again by mistake.
    if order.fraud_status != "flagged":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This order has already been reviewed."
        )
    # Deliberately does NOT touch the phone/IP blocklist — see the fraud
    # page's design notes. A review decision is metadata; blocking is a
    # separate, explicit merchant action.
    order.fraud_status = payload.decision
    return await crud.save(db, order)
