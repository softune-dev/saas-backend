"""The number shown on a locked page: "you have 42 customers".

A locked page is blurred in the dashboard, and blur is only CSS. So the real
rows must never reach the browser at all: this returns one count and nothing
else, and the dashboard draws made-up rows behind the blur.

Not gated, on purpose: it exists for the plans that lack the feature. It is
still the merchant's own data, so the site goes through the same ownership
check (and the same 404) as everything else.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import Customer, Order, Site
from app.security import CurrentUser

router = APIRouter(tags=["locked-preview"])
DB = Annotated[AsyncSession, Depends(get_db)]


class LockedPreviewOut(BaseModel):
    count: int


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("/sites/{site_id}/locked-preview/{feature}", response_model=LockedPreviewOut)
async def locked_preview(
    site_id: uuid.UUID, feature: str, user: CurrentUser, db: DB
) -> LockedPreviewOut:
    site = await _owned_site(db, user.tenant_id, site_id)
    if feature == "customers":
        model, filters = Customer, [Customer.site_id == site.id]
    elif feature == "fraud":
        # The orders waiting for review, which is what the locked page lists.
        model, filters = Order, [Order.site_id == site.id, Order.fraud_status == "flagged"]
    else:
        # A plan feature with nothing to count is as unknown here as a typo.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return LockedPreviewOut(
        count=await crud.count_scoped(db, model, user.tenant_id, filters=filters)
    )
