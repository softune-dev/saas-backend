"""Help Desk — support tickets. Tenant-scoped (not site-scoped, an account's
support history isn't split per storefront). Create + list here; the reply
flow (email-only, not a chat thread — see HelpTicketReply's docstring)
lives in app/api/superadmin.py, since only a superadmin replies.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, mailer, queue
from app.db import get_db
from app.models import HelpTicket, User
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
    ticket = await crud.save(db, ticket)

    db_user = (await db.execute(select(User).where(User.id == user.user_id))).scalar_one()
    ticket_number_display = f"TKT-{ticket.ticket_number:05d}"
    subject, html_body, text_body = mailer.ticket_created_email(
        db_user.full_name, ticket_number_display, ticket.subject, ticket.message
    )
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": db_user.email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )
    return ticket
