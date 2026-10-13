"""Superadmin view of the permanent marketing contact list (app/contacts.py,
migrations/077): everyone who ever signed up, including people whose accounts
were later deleted. Read, filter, opt out, add notes, and trigger a sync.

Cross-tenant by design and gated by SuperAdminUser, like the rest of
/superadmin. The table holds personal data (names, emails, phones), which is
why it is operator-only and why unsubscribing is honoured everywhere a
campaign is sent.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, contacts
from app.db import get_db
from app.models import MarketingContact
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin/contacts", tags=["superadmin-contacts"])
DB = Annotated[AsyncSession, Depends(get_db)]

Lifecycle = Literal["lead", "trial", "customer", "churned", "former_customer"]


class ContactOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    phone: str | None
    shop_name: str | None
    subdomain: str | None
    source: str
    lifecycle: str
    plan: str | None
    ever_paid: bool
    signed_up_at: datetime | None
    converted_at: datetime | None
    account_deleted_at: datetime | None
    last_login_at: datetime | None
    unsubscribed_at: datetime | None
    notes: str | None
    created_at: datetime


class ContactSummaryOut(BaseModel):
    total: int
    by_lifecycle: dict[str, int]
    with_phone: int
    unsubscribed: int
    added_7d: int
    emailable: int


class ContactPatch(BaseModel):
    unsubscribed: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("", response_model=Page[ContactOut])
async def list_contacts(
    admin: SuperAdminUser,
    db: DB,
    lifecycle: Lifecycle | None = None,
    q: str | None = None,
    has_phone: bool | None = None,
    unsubscribed: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if lifecycle:
        filters.append(MarketingContact.lifecycle == lifecycle)
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                MarketingContact.email.ilike(like),
                MarketingContact.full_name.ilike(like),
                MarketingContact.phone.ilike(like),
                MarketingContact.shop_name.ilike(like),
            )
        )
    if has_phone:
        filters.append(MarketingContact.phone.is_not(None))
    if unsubscribed is True:
        filters.append(MarketingContact.unsubscribed_at.is_not(None))
    elif unsubscribed is False:
        filters.append(MarketingContact.unsubscribed_at.is_(None))

    total = (await db.execute(select(func.count(MarketingContact.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(MarketingContact)
            .where(*filters)
            .order_by(MarketingContact.signed_up_at.desc().nulls_last(), MarketingContact.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/summary", response_model=ContactSummaryOut)
async def summary(admin: SuperAdminUser, db: DB) -> dict:
    by = dict(
        (
            await db.execute(
                select(MarketingContact.lifecycle, func.count(MarketingContact.id)).group_by(
                    MarketingContact.lifecycle
                )
            )
        ).all()
    )
    week = datetime.now(UTC) - timedelta(days=7)
    totals = (
        await db.execute(
            select(
                func.count(MarketingContact.id),
                func.count(MarketingContact.id).filter(MarketingContact.phone.is_not(None)),
                func.count(MarketingContact.id).filter(MarketingContact.unsubscribed_at.is_not(None)),
                func.count(MarketingContact.id).filter(MarketingContact.created_at >= week),
            )
        )
    ).one()
    return {
        "total": totals[0],
        "by_lifecycle": {k: by.get(k, 0) for k in ("lead", "trial", "customer", "churned", "former_customer")},
        "with_phone": totals[1],
        "unsubscribed": totals[2],
        "added_7d": totals[3],
        "emailable": totals[0] - totals[2],
    }


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(contact_id: uuid.UUID, payload: ContactPatch, admin: SuperAdminUser, db: DB) -> MarketingContact:
    contact = (
        await db.execute(select(MarketingContact).where(MarketingContact.id == contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    changes: dict = {}
    if payload.unsubscribed is not None:
        was = contact.unsubscribed_at is not None
        if payload.unsubscribed and not was:
            contact.unsubscribed_at = datetime.now(UTC)
            changes["unsubscribed"] = True
        elif not payload.unsubscribed and was:
            contact.unsubscribed_at = None
            changes["unsubscribed"] = False
    if payload.notes is not None and payload.notes != (contact.notes or ""):
        contact.notes = payload.notes.strip() or None
        changes["notes"] = "updated"
    await db.commit()
    await db.refresh(contact)
    if changes:
        await audit.record(admin, "contact.update", "contact", contact.id, contact.email, changes)
    return contact


@router.post("/sync")
async def sync_now(admin: SuperAdminUser, db: DB) -> dict:
    """Refresh the list right now instead of waiting for the next scheduled run."""
    result = await contacts.sync_all(db)
    await audit.record(admin, "contact.sync", "contact", None, "Marketing contacts", result)
    return result
