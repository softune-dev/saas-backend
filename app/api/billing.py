"""Invoices — read-only from the dashboard's own tenant. Nothing here
creates an invoice; that only happens at trial start (app/api/trial.py) or
a manual plan change (app/api/superadmin.py) — see migrations/053's own
docstring on why invoices are event-triggered, not a recurring billing job.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import Invoice
from app.schemas import InvoiceOut, Page
from app.security import CurrentUser

router = APIRouter(prefix="/billing", tags=["billing"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/invoices", response_model=Page[InvoiceOut])
async def list_invoices(user: CurrentUser, db: DB) -> dict:
    rows, total = await crud.list_scoped(
        db, Invoice, user.tenant_id, order_by=Invoice.issued_at.desc(), limit=100,
    )
    return {"items": rows, "total": total, "limit": 100, "offset": 0}
