"""Superadmin operations: every storefront on the platform, theme usage, a
360 view of one tenant, the live activity feed, the admin audit log, ticket
status counts and the AI credit ledger.

All read-only and cross-tenant, gated by SuperAdminUser like the rest of
/superadmin (see app/api/superadmin.py's docstring for why rule 1 does not
apply to this router).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.superadmin import site_logo_url
from app.config import settings
from app.db import get_db
from app.models import (
    AdminAuditLog,
    AiImageCreditTransaction,
    AiUsageLog,
    ChatCreditTransaction,
    EmailCampaign,
    HelpTicket,
    Invoice,
    Order,
    PaymentClaim,
    Product,
    Site,
    Template,
    Tenant,
    User,
)
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin", tags=["superadmin-ops"])
DB = Annotated[AsyncSession, Depends(get_db)]


def _site_url(site: Site) -> str:
    return f"https://{site.custom_domain or f'{site.subdomain}.{settings.site_base_domain}'}"


# ----------------------------------------------------------------------------
#  Storefronts
# ----------------------------------------------------------------------------


class StorefrontOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_plan: str
    tenant_status: str
    name: str
    subdomain: str
    custom_domain: str | None
    pending_custom_domain: str | None
    url: str
    status: str
    template_key: str | None
    template_name: str | None
    logo_url: str | None
    product_count: int
    orders_30d: int
    gmv_30d_cents: int
    onboarding_done: bool
    created_at: datetime
    published_at: datetime | None


@router.get("/sites", response_model=Page[StorefrontOut])
async def list_sites(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    status_filter: Annotated[Literal["published", "draft"] | None, Query(alias="status")] = None,
    template: str | None = None,
    domain: Literal["custom", "pending", "none"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(
            or_(Site.name.ilike(like), Site.subdomain.ilike(like), Site.custom_domain.ilike(like), Tenant.name.ilike(like))
        )
    if status_filter:
        filters.append(Site.status == status_filter)
    if template:
        filters.append(Template.key == template)
    if domain == "custom":
        filters.append(Site.custom_domain.is_not(None))
    elif domain == "pending":
        filters.append(Site.pending_custom_domain.is_not(None))
    elif domain == "none":
        filters.append(Site.custom_domain.is_(None))

    base = (
        select(Site, Tenant, Template.key, Template.name)
        .join(Tenant, Tenant.id == Site.tenant_id)
        .outerjoin(Template, Template.id == Site.template_id)
        .where(*filters)
    )
    total = (
        await db.execute(
            select(func.count(Site.id))
            .join(Tenant, Tenant.id == Site.tenant_id)
            .outerjoin(Template, Template.id == Site.template_id)
            .where(*filters)
        )
    ).scalar_one()
    rows = (await db.execute(base.order_by(Site.created_at.desc()).limit(limit).offset(offset))).all()
    site_ids = [s.id for s, *_ in rows]

    products: dict[uuid.UUID, int] = {}
    orders: dict[uuid.UUID, tuple[int, int]] = {}
    if site_ids:
        products = dict(
            (
                await db.execute(
                    select(Product.site_id, func.count(Product.id))
                    .where(Product.site_id.in_(site_ids))
                    .group_by(Product.site_id)
                )
            ).all()
        )
        since = datetime.now(UTC) - timedelta(days=30)
        orders = {
            sid: (n, int(g))
            for sid, n, g in (
                await db.execute(
                    select(Order.site_id, func.count(Order.id), func.coalesce(func.sum(Order.total_cents), 0))
                    .where(Order.site_id.in_(site_ids), Order.created_at >= since, Order.status != "cancelled")
                    .group_by(Order.site_id)
                )
            ).all()
        }

    items = []
    for site, tenant, tkey, tname in rows:
        n_orders, gmv = orders.get(site.id, (0, 0))
        items.append(
            {
                "id": site.id,
                "tenant_id": tenant.id,
                "tenant_name": tenant.name,
                "tenant_plan": tenant.plan,
                "tenant_status": tenant.status,
                "name": site.name,
                "subdomain": site.subdomain,
                "custom_domain": site.custom_domain,
                "pending_custom_domain": site.pending_custom_domain,
                "url": _site_url(site),
                "status": site.status,
                "template_key": tkey,
                "template_name": tname,
                "logo_url": site_logo_url(site.theme),
                "product_count": products.get(site.id, 0),
                "orders_30d": n_orders,
                "gmv_30d_cents": gmv,
                "onboarding_done": site.onboarding_completed_at is not None,
                "created_at": site.created_at,
                "published_at": site.published_at,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


class ThemeUsageOut(BaseModel):
    key: str
    name: str
    description: str | None
    thumbnail_url: str | None
    preview_url: str | None
    is_active: bool
    sites: int
    published: int
    paying_tenants: int
    new_30d: int


@router.get("/themes", response_model=list[ThemeUsageOut])
async def theme_usage(admin: SuperAdminUser, db: DB) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=30)
    templates = (await db.execute(select(Template).order_by(Template.name))).scalars().all()
    rows = (
        await db.execute(
            select(
                Site.template_id,
                func.count(Site.id),
                func.count(Site.id).filter(Site.status == "published"),
                func.count(func.distinct(Site.tenant_id)).filter(Tenant.plan.in_(("starter", "growth", "business"))),
                func.count(Site.id).filter(Site.created_at >= since),
            )
            .join(Tenant, Tenant.id == Site.tenant_id)
            .group_by(Site.template_id)
        )
    ).all()
    stats = {tid: (a, b, c, d) for tid, a, b, c, d in rows}
    out = []
    for t in templates:
        a, b, c, d = stats.get(t.id, (0, 0, 0, 0))
        out.append(
            {
                "key": t.key,
                "name": t.name,
                "description": t.description,
                "thumbnail_url": t.thumbnail_url,
                "preview_url": t.preview_url,
                "is_active": t.is_active,
                "sites": a,
                "published": b,
                "paying_tenants": c,
                "new_30d": d,
            }
        )
    out.sort(key=lambda x: x["sites"], reverse=True)
    return out


# ----------------------------------------------------------------------------
#  Tenant 360
# ----------------------------------------------------------------------------


class Tenant360Out(BaseModel):
    users: list[dict[str, Any]]
    sites: list[dict[str, Any]]
    invoices: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    credits: list[dict[str, Any]]
    tickets: list[dict[str, Any]]
    audit: list[dict[str, Any]]
    orders_series: list[dict[str, Any]]
    orders_30d: int
    gmv_30d_cents: int
    orders_all_time: int
    gmv_all_time_cents: int
    ai_requests_30d: int
    ai_errors_30d: int
    ai_images_30d: int
    lifetime_billed_cents: int


@router.get("/tenants/{tenant_id}/insights", response_model=Tenant360Out)
async def tenant_insights(tenant_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> dict:
    """Everything about one tenant in one call, for the tenant detail drawer."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    now = datetime.now(UTC)
    since = now - timedelta(days=30)

    users = (
        await db.execute(select(User).where(User.tenant_id == tenant_id).order_by(User.created_at))
    ).scalars().all()
    sites = (
        await db.execute(
            select(Site, Template.key, Template.name)
            .outerjoin(Template, Template.id == Site.template_id)
            .where(Site.tenant_id == tenant_id)
            .order_by(Site.created_at)
        )
    ).all()
    invoices = (
        await db.execute(
            select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.issued_at.desc()).limit(12)
        )
    ).scalars().all()
    claim_rows = (
        await db.execute(
            select(PaymentClaim)
            .where(PaymentClaim.tenant_id == tenant_id)
            .order_by(PaymentClaim.created_at.desc())
            .limit(12)
        )
    ).scalars().all()
    image_tx = (
        await db.execute(
            select(AiImageCreditTransaction)
            .where(AiImageCreditTransaction.tenant_id == tenant_id)
            .order_by(AiImageCreditTransaction.created_at.desc())
            .limit(15)
        )
    ).scalars().all()
    chat_tx = (
        await db.execute(
            select(ChatCreditTransaction)
            .where(ChatCreditTransaction.tenant_id == tenant_id)
            .order_by(ChatCreditTransaction.created_at.desc())
            .limit(15)
        )
    ).scalars().all()
    tickets = (
        await db.execute(
            select(HelpTicket).where(HelpTicket.tenant_id == tenant_id).order_by(HelpTicket.created_at.desc()).limit(10)
        )
    ).scalars().all()
    audit_rows = (
        await db.execute(
            select(AdminAuditLog)
            .where(
                or_(
                    (AdminAuditLog.target_type == "tenant") & (AdminAuditLog.target_id == str(tenant_id)),
                    AdminAuditLog.details["tenant_id"].astext == str(tenant_id),
                )
            )
            .order_by(AdminAuditLog.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    day = func.to_char(func.date_trunc("day", Order.created_at), "YYYY-MM-DD")
    order_days = {
        d: (n, int(g))
        for d, n, g in (
            await db.execute(
                select(day, func.count(Order.id), func.coalesce(func.sum(Order.total_cents), 0))
                .where(Order.tenant_id == tenant_id, Order.created_at >= since, Order.status != "cancelled")
                .group_by(day)
            )
        ).all()
    }
    series = []
    for i in range(30):
        d = (since + timedelta(days=i + 1)).date().isoformat()
        n, g = order_days.get(d, (0, 0))
        series.append({"day": d, "orders": n, "gmv_cents": g})
    all_time = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_cents), 0))
            .where(Order.tenant_id == tenant_id, Order.status != "cancelled")
        )
    ).one()
    ai = (
        await db.execute(
            select(
                func.count(AiUsageLog.id),
                func.count(AiUsageLog.id).filter(AiUsageLog.status == "error"),
                func.count(AiUsageLog.id).filter(AiUsageLog.kind.in_(("image_generate", "image_edit"))),
            ).where(AiUsageLog.tenant_id == tenant_id, AiUsageLog.created_at >= since)
        )
    ).one()
    billed = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount_cents), 0)).where(Invoice.tenant_id == tenant_id)
        )
    ).scalar_one()

    credits = [
        {"currency": "image", "delta": t.delta, "reason": t.reason, "balance_after": t.balance_after,
         "reference": t.reference, "created_at": t.created_at}
        for t in image_tx
    ] + [
        {"currency": "chat", "delta": t.delta, "reason": t.reason, "balance_after": t.balance_after,
         "reference": t.reference, "created_at": t.created_at}
        for t in chat_tx
    ]
    credits.sort(key=lambda c: c["created_at"], reverse=True)

    return {
        "users": [
            {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "phone": u.phone,
             "avatar_url": u.avatar_url, "is_active": u.is_active, "last_login_at": u.last_login_at,
             "created_at": u.created_at}
            for u in users
        ],
        "sites": [
            {"id": s.id, "name": s.name, "subdomain": s.subdomain, "custom_domain": s.custom_domain,
             "pending_custom_domain": s.pending_custom_domain, "status": s.status, "url": _site_url(s),
             "template_key": tk, "template_name": tn, "logo_url": site_logo_url(s.theme),
             "created_at": s.created_at, "published_at": s.published_at,
             "onboarding_done": s.onboarding_completed_at is not None}
            for s, tk, tn in sites
        ],
        "invoices": [
            {"id": i.id, "invoice_number": i.invoice_number, "plan": i.plan, "amount_cents": i.amount_cents,
             "period_label": i.period_label, "pdf_url": i.pdf_url, "issued_at": i.issued_at}
            for i in invoices
        ],
        "claims": [
            {"id": c.id, "kind": c.kind, "plan": c.plan, "credits": c.credits, "amount_cents": c.amount_cents,
             "trx_id": c.trx_id, "sender_number": c.sender_number, "status": c.status,
             "created_at": c.created_at, "verified_at": c.verified_at}
            for c in claim_rows
        ],
        "credits": credits[:20],
        "tickets": [
            {"id": t.id, "number": f"TKT-{t.ticket_number:05d}", "subject": t.subject, "status": t.status,
             "priority": t.priority, "created_at": t.created_at}
            for t in tickets
        ],
        "audit": [
            {"id": a.id, "actor_email": a.actor_email, "action": a.action, "details": a.details,
             "target_label": a.target_label, "created_at": a.created_at}
            for a in audit_rows
        ],
        "orders_series": series,
        "orders_30d": sum(p["orders"] for p in series),
        "gmv_30d_cents": sum(p["gmv_cents"] for p in series),
        "orders_all_time": all_time[0],
        "gmv_all_time_cents": int(all_time[1]),
        "ai_requests_30d": ai[0],
        "ai_errors_30d": ai[1],
        "ai_images_30d": ai[2],
        "lifetime_billed_cents": int(billed),
    }


# ----------------------------------------------------------------------------
#  Activity feed
# ----------------------------------------------------------------------------


class ActivityOut(BaseModel):
    kind: str
    title: str
    detail: str | None
    at: datetime
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    href: str | None


@router.get("/activity", response_model=list[ActivityOut])
async def activity(admin: SuperAdminUser, db: DB, limit: Annotated[int, Query(ge=5, le=100)] = 30) -> list[dict]:
    """The latest things that happened on the platform, newest first: signups,
    payments, tickets, invoices, campaigns and AI failures, merged from their
    own tables (no separate events table to keep in sync)."""
    per = limit
    events: list[dict] = []

    for t in (await db.execute(select(Tenant).order_by(Tenant.created_at.desc()).limit(per))).scalars():
        events.append({"kind": "signup", "title": f"{t.name} signed up", "detail": f"{t.plan} plan",
                       "at": t.created_at, "tenant_id": t.id, "tenant_name": t.name,
                       "href": f"/superadmin/tenants?q={t.name}"})

    for c, name in (
        await db.execute(
            select(PaymentClaim, Tenant.name).join(Tenant, Tenant.id == PaymentClaim.tenant_id)
            .order_by(PaymentClaim.created_at.desc()).limit(per)
        )
    ).all():
        label = c.plan if c.kind == "plan" else f"{c.credits} {c.kind.replace('_', ' ')}"
        events.append({"kind": f"payment_{c.status}", "title": f"{name} submitted a payment",
                       "detail": f"৳{c.amount_cents // 100:,} for {label}, {c.status}",
                       "at": c.created_at, "tenant_id": c.tenant_id, "tenant_name": name,
                       "href": "/superadmin/billing"})

    for t, name in (
        await db.execute(
            select(HelpTicket, Tenant.name).join(Tenant, Tenant.id == HelpTicket.tenant_id)
            .order_by(HelpTicket.created_at.desc()).limit(per)
        )
    ).all():
        events.append({"kind": "ticket", "title": f"New ticket from {name}", "detail": t.subject,
                       "at": t.created_at, "tenant_id": t.tenant_id, "tenant_name": name,
                       "href": "/superadmin/tickets"})

    for i, name in (
        await db.execute(
            select(Invoice, Tenant.name).join(Tenant, Tenant.id == Invoice.tenant_id)
            .order_by(Invoice.issued_at.desc()).limit(per)
        )
    ).all():
        events.append({"kind": "invoice", "title": f"Invoice {i.invoice_number} issued",
                       "detail": f"{name}, ৳{i.amount_cents // 100:,} ({i.plan})",
                       "at": i.issued_at, "tenant_id": i.tenant_id, "tenant_name": name,
                       "href": "/superadmin/billing"})

    for c in (await db.execute(select(EmailCampaign).order_by(EmailCampaign.created_at.desc()).limit(per))).scalars():
        events.append({"kind": "campaign", "title": f"Campaign sent: {c.subject}",
                       "detail": f"{c.recipient_count} recipients", "at": c.created_at,
                       "tenant_id": None, "tenant_name": None, "href": "/superadmin/marketing"})

    for a, name in (
        await db.execute(
            select(AiUsageLog, Tenant.name).join(Tenant, Tenant.id == AiUsageLog.tenant_id)
            .where(AiUsageLog.status == "error")
            .order_by(AiUsageLog.created_at.desc()).limit(per)
        )
    ).all():
        events.append({"kind": "ai_error", "title": f"AI request failed for {name}",
                       "detail": (a.error or a.kind)[:140], "at": a.created_at,
                       "tenant_id": a.tenant_id, "tenant_name": name, "href": "/superadmin/ai-usage"})

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


# ----------------------------------------------------------------------------
#  Audit log
# ----------------------------------------------------------------------------


class AuditOut(ORMModel):
    id: uuid.UUID
    actor_email: str
    action: str
    target_type: str
    target_id: str | None
    target_label: str | None
    details: dict
    created_at: datetime


@router.get("/audit", response_model=Page[AuditOut])
async def audit_log(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(AdminAuditLog.actor_email.ilike(like), AdminAuditLog.target_label.ilike(like),
                           AdminAuditLog.action.ilike(like)))
    if action:
        filters.append(AdminAuditLog.action.like(f"{action}%"))
    if target_type:
        filters.append(AdminAuditLog.target_type == target_type)
    total = (await db.execute(select(func.count(AdminAuditLog.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(AdminAuditLog).where(*filters).order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


# ----------------------------------------------------------------------------
#  Ticket counts and the credit ledger
# ----------------------------------------------------------------------------


@router.get("/tickets/counts", response_model=dict[str, int])
async def ticket_counts(admin: SuperAdminUser, db: DB) -> dict[str, int]:
    rows = (await db.execute(select(HelpTicket.status, func.count(HelpTicket.id)).group_by(HelpTicket.status))).all()
    out = {s: n for s, n in rows}
    out["all"] = sum(out.values())
    return out


class LedgerOut(BaseModel):
    currency: str
    tenant_id: uuid.UUID
    tenant_name: str
    delta: int
    reason: str
    balance_after: int
    reference: str | None
    created_at: datetime


@router.get("/credits", response_model=list[LedgerOut])
async def credit_ledger(
    admin: SuperAdminUser,
    db: DB,
    currency: Literal["image", "chat", "all"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[dict]:
    out: list[dict] = []
    for cur, model in (("image", AiImageCreditTransaction), ("chat", ChatCreditTransaction)):
        if currency not in (cur, "all"):
            continue
        rows = (
            await db.execute(
                select(model, Tenant.name).join(Tenant, Tenant.id == model.tenant_id)
                .order_by(model.created_at.desc()).limit(limit)
            )
        ).all()
        out += [
            {"currency": cur, "tenant_id": t.tenant_id, "tenant_name": n, "delta": t.delta, "reason": t.reason,
             "balance_after": t.balance_after, "reference": t.reference, "created_at": t.created_at}
            for t, n in rows
        ]
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out[:limit]
