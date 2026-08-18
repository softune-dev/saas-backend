"""Dashboard bell notifications — read and mark-read only.

Rows are created as a side effect of other writes (see
app/notifications.py's notify(), called from public.py's create_public_order
and sites.py's publish_site/unpublish_site) — there is no POST here for
creating one directly.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import Notification, Site
from app.schemas import NotificationOut
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/notifications", tags=["notifications"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[Notification]:
    await crud.get_scoped(db, Site, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, Notification, user.tenant_id,
        filters=[Notification.site_id == site_id],
        order_by=Notification.created_at.desc(),
        limit=30,  # the bell dropdown only ever shows the most recent handful
    )
    return rows


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    site_id: uuid.UUID, notification_id: uuid.UUID, user: CurrentUser, db: DB
) -> Notification:
    notification = await crud.get_scoped(db, Notification, user.tenant_id, notification_id)
    if notification.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(notification)
    return notification


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(site_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    await crud.get_scoped(db, Site, user.tenant_id, site_id)
    await db.execute(
        update(Notification)
        .where(
            Notification.tenant_id == user.tenant_id,
            Notification.site_id == site_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
