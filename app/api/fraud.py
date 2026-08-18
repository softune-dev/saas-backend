"""Fraud blocklist — a merchant-maintained phone number list.

Backs dashboard/lib/api/fraud.ts and Settings → Fraud Protection
(dashboard/components/fraud/). Deliberately NOT a computed risk score: a
new site has no order history to score a customer against, so the only
thing that works from day one is letting the merchant list numbers they
already know are bad (from Facebook, past experience, word of mouth).

Rules (hold_first_high_value / flag_burst_orders / block_blocklist) are NOT
here — they live in sites.fraud_rules, read/written through the existing
generic PATCH /sites/{id} (see app/api/sites.py's update_site), same as
shipping/faqs/legal. This module only owns the blocklist rows.

NOT YET ENFORCED: like payments.py, there is no real public checkout
endpoint yet to check a phone against this list at order time — see
CheckoutPageClient.tsx, which never calls the API. This is storage only
until that endpoint exists.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import FraudBlocklistEntry, Site
from app.schemas import FraudBlocklistEntryCreate, FraudBlocklistEntryOut
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/fraud/blocklist", tags=["fraud"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("", response_model=list[FraudBlocklistEntryOut])
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


@router.post("", response_model=FraudBlocklistEntryOut, status_code=status.HTTP_201_CREATED)
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


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_blocklist(
    site_id: uuid.UUID, entry_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    entry = await crud.get_scoped(db, FraudBlocklistEntry, user.tenant_id, entry_id)
    if entry.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blocklist entry not found")
    await crud.delete(db, entry)
