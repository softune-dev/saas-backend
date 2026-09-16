"""Invoices — read-only from the dashboard's own tenant. Nothing here
creates an invoice; that only happens at trial start (app/api/trial.py) or
a manual plan change (app/api/superadmin.py) — see migrations/053's own
docstring on why invoices are event-triggered, not a recurring billing job.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, invoices as invoices_module, mailer
from app.config import settings
from app.db import get_db
from app.models import Invoice, Site, Tenant, User
from app.schemas import InvoiceOut, ManualPaymentSubmit, Page
from app.security import CurrentUser

router = APIRouter(prefix="/billing", tags=["billing"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/invoices", response_model=Page[InvoiceOut])
async def list_invoices(user: CurrentUser, db: DB) -> dict:
    rows, total = await crud.list_scoped(
        db, Invoice, user.tenant_id, order_by=Invoice.issued_at.desc(), limit=100,
    )
    return {"items": rows, "total": total, "limit": 100, "offset": 0}


@router.post("/manual-payment", status_code=status.HTTP_202_ACCEPTED)
async def submit_manual_payment(payload: ManualPaymentSubmit, user: CurrentUser, db: DB) -> dict:
    """The dashboard Billing page's self-serve "I already sent the money"
    step — there's still no payment gateway (see
    dashboard/components/billing/billing-data.ts's own docstring), so a
    real plan change is still applied by hand from Superadmin, but the
    merchant no longer has to compose their own email to say they paid.

    Deliberately stores nothing: this email IS the record (two copies —
    SUPPORT and settings.billing_notify_email — see
    mailer.manual_payment_submitted_email's own docstring for why both).
    trx_id gets cross-checked against the real bKash merchant statement by
    a person before anyone touches Tenant.plan.
    """
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    owner = (
        await db.execute(
            select(User).where(User.tenant_id == user.tenant_id, User.role == "owner")
        )
    ).scalars().first()
    site = (
        await db.execute(select(Site).where(Site.tenant_id == user.tenant_id).limit(1))
    ).scalars().first()

    subject, html_body, text_body = mailer.manual_payment_submitted_email(
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        current_plan=tenant.plan,
        trial_expires_at=tenant.trial_expires_at.isoformat() if tenant.trial_expires_at else None,
        owner_name=owner.full_name if owner else None,
        owner_email=owner.email if owner else "—",
        site_subdomain=site.subdomain if site else None,
        requested_plan_name=invoices_module.PLAN_NAMES.get(payload.plan, payload.plan),
        amount_taka=invoices_module.PLAN_PRICES_CENTS.get(payload.plan, 0) // 100,
        sender_number=payload.sender_number.strip(),
        trx_id=payload.trx_id.strip(),
        note=payload.note.strip() if payload.note else None,
    )
    sent_support = await mailer.send_email(mailer.SUPPORT, subject, html_body, text_body)
    sent_copy = await mailer.send_email(settings.billing_notify_email, subject, html_body, text_body)
    if not sent_support and not sent_copy:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't send this right now — email support@softunebd.com directly with your Transaction ID.",
        )
    return {"received": True}
