"""Superadmin billing: revenue at a glance, the manual payment inbox, every
invoice, and upcoming renewals.

THE PAYMENT INBOX is the important part. Merchants pay by bKash by hand and
submit the TrxID (a PaymentClaim). The SMS webhook approves most of them
automatically, but a claim whose SMS never arrived, or whose amount did not
match, used to sit at "pending" with no way to act on it except a database
query. Approve / Reject here closes that gap. Approve goes through the exact
same app/claims.py path the webhook uses, so a hand-approved payment leaves
the same plan, invoice and credit state as an automatic one.

MRR is computed from plan prices (app/invoices.py's PLAN_PRICES_CENTS) for
tenants on an active paid plan. It is the recurring revenue the platform is
owed per month, not cash received; "billed" figures come from real invoices.

Cross-tenant and gated by SuperAdminUser, like the rest of /superadmin.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, claims, invoices as invoices_module
from app.api.superadmin import site_logo_url
from app.db import get_db
from app.models import Invoice, PaymentClaim, Site, Tenant, User
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin/billing", tags=["superadmin-billing"])
DB = Annotated[AsyncSession, Depends(get_db)]

PAID_PLANS = ("starter", "growth", "business")


async def _logos(db: AsyncSession, tenant_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    """First storefront logo per tenant, for the tables."""
    out: dict[uuid.UUID, str | None] = {tid: None for tid in tenant_ids}
    if not tenant_ids:
        return out
    rows = (
        await db.execute(
            select(Site.tenant_id, Site.theme)
            .where(Site.tenant_id.in_(tenant_ids))
            .order_by(Site.created_at)
        )
    ).all()
    for tid, theme in rows:
        if out.get(tid) is None:
            out[tid] = site_logo_url(theme)
    return out


async def _owner_emails(db: AsyncSession, tenant_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not tenant_ids:
        return {}
    rows = (
        await db.execute(
            select(User.tenant_id, User.email).where(
                User.tenant_id.in_(tenant_ids), User.role == "owner"
            )
        )
    ).all()
    return {tid: email for tid, email in rows}


# ----------------------------------------------------------------------------
#  Summary
# ----------------------------------------------------------------------------


class MonthPoint(BaseModel):
    month: str
    billed_cents: int
    invoices: int


class BillingSummaryOut(BaseModel):
    mrr_cents: int
    arr_cents: int
    at_risk_mrr_cents: int
    paying_tenants: int
    paying_by_plan: dict[str, int]
    plan_prices_cents: dict[str, int]
    billed_30d_cents: int
    billed_prev_30d_cents: int
    billed_all_time_cents: int
    conversions_30d: int
    pending_claims: int
    pending_claims_cents: int
    verified_claims_30d: int
    verified_claims_30d_cents: int
    renewals_due_7d: int
    overdue_tenants: int
    months: list[MonthPoint]


@router.get("/summary", response_model=BillingSummaryOut)
async def billing_summary(admin: SuperAdminUser, db: DB) -> dict:
    now = datetime.now(UTC)
    d30, d60 = now - timedelta(days=30), now - timedelta(days=60)
    prices = invoices_module.PLAN_PRICES_CENTS

    plan_rows = (
        await db.execute(
            select(Tenant.plan, Tenant.status, func.count(Tenant.id))
            .where(Tenant.plan.in_(PAID_PLANS))
            .group_by(Tenant.plan, Tenant.status)
        )
    ).all()
    paying_by_plan: dict[str, int] = {p: 0 for p in PAID_PLANS}
    mrr = at_risk = 0
    for plan, tenant_status, n in plan_rows:
        if tenant_status == "active":
            paying_by_plan[plan] += n
            mrr += prices.get(plan, 0) * n
        elif tenant_status == "payment_overdue":
            at_risk += prices.get(plan, 0) * n

    async def billed_between(start: datetime | None, end: datetime | None) -> int:
        q = select(func.coalesce(func.sum(Invoice.amount_cents), 0))
        if start is not None:
            q = q.where(Invoice.issued_at >= start)
        if end is not None:
            q = q.where(Invoice.issued_at < end)
        return int((await db.execute(q)).scalar_one())

    # A conversion is a tenant whose FIRST paid invoice falls in the window.
    first_paid = (
        select(Invoice.tenant_id, func.min(Invoice.issued_at).label("first_at"))
        .where(Invoice.amount_cents > 0)
        .group_by(Invoice.tenant_id)
        .subquery()
    )
    conversions = (
        await db.execute(select(func.count()).select_from(first_paid).where(first_paid.c.first_at >= d30))
    ).scalar_one()

    pending = (
        await db.execute(
            select(func.count(PaymentClaim.id), func.coalesce(func.sum(PaymentClaim.amount_cents), 0))
            .where(PaymentClaim.status == "pending")
        )
    ).one()
    verified = (
        await db.execute(
            select(func.count(PaymentClaim.id), func.coalesce(func.sum(PaymentClaim.amount_cents), 0))
            .where(PaymentClaim.status == "verified", PaymentClaim.verified_at >= d30)
        )
    ).one()

    renewals = (
        await db.execute(
            select(func.count(Tenant.id)).where(
                Tenant.plan.in_(PAID_PLANS),
                Tenant.plan_renews_at.is_not(None),
                Tenant.plan_renews_at <= now + timedelta(days=7),
                Tenant.plan_renews_at >= now,
            )
        )
    ).scalar_one()
    overdue = (
        await db.execute(select(func.count(Tenant.id)).where(Tenant.status == "payment_overdue"))
    ).scalar_one()

    month = func.to_char(func.date_trunc("month", Invoice.issued_at), "YYYY-MM")
    month_rows = (
        await db.execute(
            select(month, func.coalesce(func.sum(Invoice.amount_cents), 0), func.count(Invoice.id))
            .where(Invoice.issued_at >= now - timedelta(days=365))
            .group_by(month)
        )
    ).all()
    by_month = {m: (int(c), n) for m, c, n in month_rows}
    months = []
    y, mth = now.year, now.month
    for _ in range(12):
        key = f"{y:04d}-{mth:02d}"
        billed, n = by_month.get(key, (0, 0))
        months.append({"month": key, "billed_cents": billed, "invoices": n})
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    months.reverse()

    return {
        "mrr_cents": mrr,
        "arr_cents": mrr * 12,
        "at_risk_mrr_cents": at_risk,
        "paying_tenants": sum(paying_by_plan.values()),
        "paying_by_plan": paying_by_plan,
        "plan_prices_cents": {p: prices.get(p, 0) for p in PAID_PLANS},
        "billed_30d_cents": await billed_between(d30, None),
        "billed_prev_30d_cents": await billed_between(d60, d30),
        "billed_all_time_cents": await billed_between(None, None),
        "conversions_30d": conversions,
        "pending_claims": pending[0],
        "pending_claims_cents": int(pending[1]),
        "verified_claims_30d": verified[0],
        "verified_claims_30d_cents": int(verified[1]),
        "renewals_due_7d": renewals,
        "overdue_tenants": overdue,
        "months": months,
    }


# ----------------------------------------------------------------------------
#  Payment claims (the manual payment inbox)
# ----------------------------------------------------------------------------


class ClaimOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_logo_url: str | None
    owner_email: str | None
    kind: str
    plan: str | None
    pack_id: str | None
    credits: int | None
    amount_cents: int
    sender_number: str
    trx_id: str
    note: str | None
    status: str
    matched_sms: str | None
    verified_at: datetime | None
    created_at: datetime


class RejectIn(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


def _claim_out(c: PaymentClaim, name: str, logo: str | None, owner: str | None) -> dict:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "tenant_name": name,
        "tenant_logo_url": logo,
        "owner_email": owner,
        "kind": c.kind,
        "plan": c.plan,
        "pack_id": c.pack_id,
        "credits": c.credits,
        "amount_cents": c.amount_cents,
        "sender_number": c.sender_number,
        "trx_id": c.trx_id,
        "note": c.note,
        "status": c.status,
        "matched_sms": c.matched_sms,
        "verified_at": c.verified_at,
        "created_at": c.created_at,
    }


async def _claim_row(db: AsyncSession, claim_id: uuid.UUID) -> dict:
    row = (
        await db.execute(
            select(PaymentClaim, Tenant.name)
            .join(Tenant, Tenant.id == PaymentClaim.tenant_id)
            .where(PaymentClaim.id == claim_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment claim not found")
    claim, name = row
    logos = await _logos(db, [claim.tenant_id])
    owners = await _owner_emails(db, [claim.tenant_id])
    return _claim_out(claim, name, logos.get(claim.tenant_id), owners.get(claim.tenant_id))


@router.get("/claims", response_model=Page[ClaimOut])
async def list_claims(
    admin: SuperAdminUser,
    db: DB,
    status_filter: Annotated[Literal["pending", "verified", "rejected", "all"], Query(alias="status")] = "pending",
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if status_filter != "all":
        filters.append(PaymentClaim.status == status_filter)
    if q:
        like = f"%{q}%"
        filters.append(
            or_(PaymentClaim.trx_id.ilike(like), PaymentClaim.sender_number.ilike(like), Tenant.name.ilike(like))
        )
    total = (
        await db.execute(
            select(func.count(PaymentClaim.id))
            .join(Tenant, Tenant.id == PaymentClaim.tenant_id)
            .where(*filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(PaymentClaim, Tenant.name)
            .join(Tenant, Tenant.id == PaymentClaim.tenant_id)
            .where(*filters)
            .order_by(PaymentClaim.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    ids = list({c.tenant_id for c, _ in rows})
    logos = await _logos(db, ids)
    owners = await _owner_emails(db, ids)
    return {
        "items": [_claim_out(c, n, logos.get(c.tenant_id), owners.get(c.tenant_id)) for c, n in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/claims/{claim_id}/approve", response_model=ClaimOut)
async def approve_claim(claim_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> dict:
    """A person checked the bKash statement and confirms the money arrived.
    Same atomic pending -> verified flip as the webhook, so an approve and an
    SMS match racing each other can never both apply the purchase."""
    result = await db.execute(
        update(PaymentClaim)
        .where(PaymentClaim.id == claim_id, PaymentClaim.status == "pending")
        .values(
            status="verified",
            matched_sms=f"Approved by {await audit.email_of(admin)}",
            verified_at=datetime.now(UTC),
        )
        .returning(PaymentClaim)
    )
    claim = result.scalar_one_or_none()
    if claim is None:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This claim is no longer pending.")
    await db.commit()
    await claims.apply_verified_claim(db, claim)
    out = await _claim_row(db, claim_id)
    await audit.record(
        admin, "payment.approve", "tenant", claim.tenant_id, out["tenant_name"],
        {"trx_id": claim.trx_id, "kind": claim.kind, "plan": claim.plan,
         "credits": claim.credits, "amount_cents": claim.amount_cents},
    )
    return out


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
async def reject_claim(claim_id: uuid.UUID, payload: RejectIn, admin: SuperAdminUser, db: DB) -> dict:
    """No money arrived (or the wrong amount did). Nothing is applied; the
    reason is kept on the claim for any follow-up with the merchant."""
    result = await db.execute(
        update(PaymentClaim)
        .where(PaymentClaim.id == claim_id, PaymentClaim.status == "pending")
        .values(
            status="rejected",
            matched_sms=f"Rejected by {await audit.email_of(admin)}: {payload.reason}",
        )
        .returning(PaymentClaim)
    )
    claim = result.scalar_one_or_none()
    if claim is None:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This claim is no longer pending.")
    await db.commit()
    out = await _claim_row(db, claim_id)
    await audit.record(
        admin, "payment.reject", "tenant", claim.tenant_id, out["tenant_name"],
        {"trx_id": claim.trx_id, "reason": payload.reason, "amount_cents": claim.amount_cents},
    )
    return out


# ----------------------------------------------------------------------------
#  Invoices and renewals
# ----------------------------------------------------------------------------


class AdminInvoiceOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_logo_url: str | None
    invoice_number: str
    plan: str
    amount_cents: int
    currency: str
    period_label: str
    pdf_url: str | None
    issued_at: datetime


@router.get("/invoices", response_model=Page[AdminInvoiceOut])
async def list_invoices(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(Invoice.invoice_number.ilike(like), Tenant.name.ilike(like)))
    if tenant_id:
        filters.append(Invoice.tenant_id == tenant_id)
    total = (
        await db.execute(
            select(func.count(Invoice.id)).join(Tenant, Tenant.id == Invoice.tenant_id).where(*filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(Invoice, Tenant.name)
            .join(Tenant, Tenant.id == Invoice.tenant_id)
            .where(*filters)
            .order_by(Invoice.issued_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    logos = await _logos(db, list({i.tenant_id for i, _ in rows}))
    return {
        "items": [
            {
                "id": i.id,
                "tenant_id": i.tenant_id,
                "tenant_name": n,
                "tenant_logo_url": logos.get(i.tenant_id),
                "invoice_number": i.invoice_number,
                "plan": i.plan,
                "amount_cents": i.amount_cents,
                "currency": i.currency,
                "period_label": i.period_label,
                "pdf_url": i.pdf_url,
                "issued_at": i.issued_at,
            }
            for i, n in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class RenewalOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_logo_url: str | None
    owner_email: str | None
    plan: str
    status: str
    amount_cents: int
    renews_at: datetime
    days_left: int


@router.get("/renewals", response_model=list[RenewalOut])
async def list_renewals(
    admin: SuperAdminUser,
    db: DB,
    days: Annotated[int, Query(ge=1, le=60)] = 14,
) -> list[dict]:
    """Paid tenants whose renewal is overdue or falls within `days`."""
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(Tenant)
            .where(
                Tenant.plan.in_(PAID_PLANS),
                Tenant.plan_renews_at.is_not(None),
                Tenant.plan_renews_at <= now + timedelta(days=days),
            )
            .order_by(Tenant.plan_renews_at)
            .limit(100)
        )
    ).scalars().all()
    ids = [t.id for t in rows]
    logos = await _logos(db, ids)
    owners = await _owner_emails(db, ids)
    prices = invoices_module.PLAN_PRICES_CENTS
    return [
        {
            "tenant_id": t.id,
            "tenant_name": t.name,
            "tenant_logo_url": logos.get(t.id),
            "owner_email": owners.get(t.id),
            "plan": t.plan,
            "status": t.status,
            "amount_cents": prices.get(t.plan, 0),
            "renews_at": t.plan_renews_at,
            "days_left": (t.plan_renews_at - now).days,
        }
        for t in rows
    ]
