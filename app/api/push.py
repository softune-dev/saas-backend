"""Web push subscription management — see app/push.py for the actual send
logic. This module only stores/removes subscriptions.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import PushSubscription, Site
from app.schemas import PushSubscribeIn, PushUnsubscribeIn
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/push", tags=["push"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(
    site_id: uuid.UUID, payload: PushSubscribeIn, user: CurrentUser, db: DB
) -> None:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    existing = (
        await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
        )
    ).scalar_one_or_none()

    if existing:
        existing.site_id = site.id
        existing.tenant_id = site.tenant_id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
    else:
        db.add(
            PushSubscription(
                tenant_id=site.tenant_id,
                site_id=site.id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
            )
        )
    await db.commit()


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    site_id: uuid.UUID, payload: PushUnsubscribeIn, user: CurrentUser, db: DB
) -> None:
    await crud.get_scoped(db, Site, user.tenant_id, site_id)
    await db.execute(
        delete(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    await db.commit()
