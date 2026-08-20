"""Help Desk — support tickets. Tenant-scoped (not site-scoped, an account's
support history isn't split per storefront). No admin/agent reply flow yet
(soft launch, see HelpTicket's docstring) — this is create + list only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import HelpTicket
from app.schemas import HelpTicketCreate, HelpTicketOut, Page
from app.security import CurrentUser

router = APIRouter(prefix="/help/tickets", tags=["help-desk"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=Page[HelpTicketOut])
async def list_tickets(
    user: CurrentUser,
    db: DB,
    limit: int = 100,
    offset: int = 0,
):
    rows, total = await crud.list_scoped(
        db, HelpTicket, user.tenant_id,
        order_by=HelpTicket.created_at.desc(),
        limit=limit,
        offset=offset,
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.post("", response_model=HelpTicketOut, status_code=201)
async def create_ticket(
    payload: HelpTicketCreate, user: CurrentUser, db: DB
) -> HelpTicket:
    ticket = HelpTicket(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        subject=payload.subject,
        category=payload.category,
        priority=payload.priority,
        message=payload.message,
    )
    return await crud.save(db, ticket)
