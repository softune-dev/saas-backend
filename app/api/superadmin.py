"""Platform operator panel — cross-tenant. Replaces scripts/create_account.py
and manual DB lookups with real, audited (via SuperAdminUser's own login)
endpoints.

WHY THIS IS THE ONE PLACE CROSS-TENANT SELECTs ARE ALLOWED: CLAUDE.md's rule
1 ("never write `select(Model).where(Model.id == x)` in a router for a
tenant-owned table") exists to stop an accidental data leak between two
CUSTOMERS. This router has no customer's own tenant_id in its request path at
all — every route is gated by SuperAdminUser (app/security.py's
require_superadmin), a platform-operator identity, not a tenant one. Cross-
tenant visibility is the entire point here, not a bypass of the rule; it's
a deliberately separate, narrowly-scoped exception to it.

Scope, kept deliberately small per the request that started this: list/create
tenants and users, and the basic account-lifecycle actions (deactivate, reset
password, change plan). No dashboard-style resource management (products,
orders, etc.) — an operator who needs to see a customer's storefront data
still does that the same way as today, not through here.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, mailer, queue
from app.db import get_db
from app.models import (
    Category,
    CourierConnection,
    HelpTicket,
    HelpTicketReply,
    Lead,
    Order,
    PaymentConnection,
    Product,
    Site,
    Tenant,
    User,
)
from app.schemas import (
    HelpTicketReplyIn,
    HelpTicketReplyOut,
    Page,
    SuperAdminAccountCreateIn,
    SuperAdminConvertLeadIn,
    SuperAdminLeadOut,
    SuperAdminStatsOut,
    SuperAdminTenantOut,
    SuperAdminTenantUpdate,
    SuperAdminTicketOut,
    SuperAdminTicketUpdate,
    SuperAdminUserCreateIn,
    SuperAdminUserOut,
    SuperAdminUserUpdate,
)
from app.security import SuperAdminUser, hash_password, revoke_all_user_tokens

router = APIRouter(prefix="/superadmin", tags=["superadmin"])
DB = Annotated[AsyncSession, Depends(get_db)]


# =============================================================================
#  Overview
# =============================================================================


@router.get("/stats", response_model=SuperAdminStatsOut)
async def get_stats(admin: SuperAdminUser, db: DB) -> dict:
    """Cheap aggregate counts for the /superadmin overview page. Every query
    here is a COUNT/GROUP BY — no row data leaves this endpoint, so there's
    nothing here that needs pagination or gets slower as the platform grows
    in any way that matters at this scale."""
    total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar_one()
    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    active_users = (
        await db.execute(select(func.count(User.id)).where(User.is_active))
    ).scalar_one()

    plan_rows = (
        await db.execute(select(Tenant.plan, func.count(Tenant.id)).group_by(Tenant.plan))
    ).all()
    status_rows = (
        await db.execute(select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status))
    ).all()

    week_ago = func.now() - text("interval '7 days'")
    new_tenants_7d = (
        await db.execute(select(func.count(Tenant.id)).where(Tenant.created_at >= week_ago))
    ).scalar_one()

    total_leads = (await db.execute(select(func.count(Lead.id)))).scalar_one()
    lead_status_rows = (
        await db.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status))
    ).all()

    return {
        "total_tenants": total_tenants,
        "total_users": total_users,
        "active_users": active_users,
        "new_tenants_7d": new_tenants_7d,
        "tenants_by_plan": dict(plan_rows),
        "tenants_by_status": dict(status_rows),
        "total_leads": total_leads,
        "leads_by_status": dict(lead_status_rows),
    }


# =============================================================================
#  Tenants
# =============================================================================


async def _tenant_aggregates(db: AsyncSession, tenant_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """One grouped COUNT per table, filtered to just this page's tenant ids,
    instead of a query per tenant per table (N+1) — for 50 tenants x 7
    metrics that's 7 queries total here, not 350."""
    out = {tid: {
        "site_count": 0, "category_count": 0, "product_count": 0,
        "order_count": 0, "user_count": 0,
        "payment_providers": [], "courier_providers": [],
    } for tid in tenant_ids}
    if not tenant_ids:
        return out

    count_specs = [
        (Site, "site_count"), (Category, "category_count"), (Product, "product_count"),
        (Order, "order_count"), (User, "user_count"),
    ]
    for model, key in count_specs:
        rows = (
            await db.execute(
                select(model.tenant_id, func.count())
                .where(model.tenant_id.in_(tenant_ids))
                .group_by(model.tenant_id)
            )
        ).all()
        for tid, n in rows:
            out[tid][key] = n

    for model, key in [(PaymentConnection, "payment_providers"), (CourierConnection, "courier_providers")]:
        rows = (
            await db.execute(
                select(model.tenant_id, model.provider)
                .where(model.tenant_id.in_(tenant_ids))
                .distinct()
            )
        ).all()
        for tid, provider in rows:
            out[tid][key].append(provider)

    return out


def _tenant_out(tenant: Tenant, aggregates: dict) -> dict:
    return {
        "id": tenant.id, "slug": tenant.slug, "name": tenant.name, "plan": tenant.plan,
        "status": tenant.status, "created_at": tenant.created_at, "business": tenant.business,
        **aggregates,
    }


@router.get("/tenants", response_model=Page[SuperAdminTenantOut])
async def list_tenants(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    limit: Annotated[int, "1-100"] = 50,
    offset: int = 0,
) -> dict:
    filters = []
    if q:
        filters.append(or_(Tenant.name.ilike(f"%{q}%"), Tenant.slug.ilike(f"%{q}%")))

    total = (await db.execute(select(func.count(Tenant.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(Tenant).where(*filters).order_by(Tenant.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    aggregates = await _tenant_aggregates(db, [t.id for t in rows])
    items = [_tenant_out(t, aggregates[t.id]) for t in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/tenants/{tenant_id}", response_model=SuperAdminTenantOut)
async def get_tenant(tenant_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> dict:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    aggregates = (await _tenant_aggregates(db, [tenant.id]))[tenant.id]
    return _tenant_out(tenant, aggregates)


@router.post(
    "/tenants", response_model=SuperAdminTenantOut, status_code=status.HTTP_201_CREATED
)
async def create_account(
    payload: SuperAdminAccountCreateIn, admin: SuperAdminUser, db: DB
) -> dict:
    """Provisions a full paid account — same crud helper
    scripts/create_account.py calls, so "creating an account" still means
    exactly one thing no matter which caller triggers it."""
    user, _site = await crud.create_tenant_owner_and_site(
        db,
        email=payload.email,
        password=payload.password,
        workspace_name=payload.workspace_name,
        plan=payload.plan,
        template_key=payload.template_key,
        site_name=payload.site_name,
        subdomain=payload.subdomain,
        full_name=payload.full_name,
    )
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    aggregates = (await _tenant_aggregates(db, [tenant.id]))[tenant.id]
    return _tenant_out(tenant, aggregates)


@router.patch("/tenants/{tenant_id}", response_model=SuperAdminTenantOut)
async def update_tenant(
    tenant_id: uuid.UUID, payload: SuperAdminTenantUpdate, admin: SuperAdminUser, db: DB
) -> dict:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    tenant = crud.apply_updates(tenant, payload.model_dump(exclude_unset=True))
    tenant = await crud.save(db, tenant)
    aggregates = (await _tenant_aggregates(db, [tenant.id]))[tenant.id]
    return _tenant_out(tenant, aggregates)


# =============================================================================
#  Help desk — email-only reply flow, deliberately not a chat thread. See
#  HelpTicketReply's docstring (app/models.py).
# =============================================================================


@router.get("/tickets", response_model=Page[SuperAdminTicketOut])
async def list_tickets(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    status_filter: str | None = None,
    limit: Annotated[int, "1-100"] = 50,
    offset: int = 0,
) -> dict:
    filters = []
    if q:
        filters.append(or_(HelpTicket.subject.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    if status_filter:
        filters.append(HelpTicket.status == status_filter)

    base_query = select(HelpTicket, Tenant.name, User.email).join(
        Tenant, HelpTicket.tenant_id == Tenant.id
    ).join(User, HelpTicket.user_id == User.id)

    total = (
        await db.execute(
            select(func.count(HelpTicket.id))
            .select_from(HelpTicket)
            .join(User, HelpTicket.user_id == User.id)
            .where(*filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            base_query.where(*filters).order_by(HelpTicket.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).all()
    items = [
        SuperAdminTicketOut(
            id=t.id, ticket_number=t.ticket_number, tenant_id=t.tenant_id, tenant_name=tenant_name,
            user_email=user_email, subject=t.subject, category=t.category, priority=t.priority,
            status=t.status, message=t.message, created_at=t.created_at, updated_at=t.updated_at,
        )
        for t, tenant_name, user_email in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/tickets/{ticket_id}/replies", response_model=list[HelpTicketReplyOut])
async def list_ticket_replies(ticket_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> list[HelpTicketReply]:
    ticket = (await db.execute(select(HelpTicket).where(HelpTicket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return (
        await db.execute(
            select(HelpTicketReply).where(HelpTicketReply.ticket_id == ticket_id)
            .order_by(HelpTicketReply.created_at)
        )
    ).scalars().all()


@router.post(
    "/tickets/{ticket_id}/replies",
    response_model=HelpTicketReplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def reply_to_ticket(
    ticket_id: uuid.UUID, payload: HelpTicketReplyIn, admin: SuperAdminUser, db: DB
) -> HelpTicketReply:
    """The whole reply flow: store it (superadmin panel's paper trail) and
    email it to the ticket's owner — one outbound message, not a chat
    bubble. Also flips status to "Replied" so the ticket list itself shows
    it's been handled, without needing to open it."""
    ticket = (await db.execute(select(HelpTicket).where(HelpTicket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    reply = await crud.save(db, HelpTicketReply(ticket_id=ticket_id, message=payload.message))

    ticket.status = "Replied"
    await crud.save(db, ticket)

    ticket_user = (await db.execute(select(User).where(User.id == ticket.user_id))).scalar_one()
    ticket_number_display = f"TKT-{ticket.ticket_number:05d}"
    subject, html_body, text_body = mailer.ticket_reply_email(
        ticket_user.full_name, ticket_number_display, ticket.subject, payload.message
    )
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": ticket_user.email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )
    return reply


@router.patch("/tickets/{ticket_id}", response_model=SuperAdminTicketOut)
async def update_ticket(
    ticket_id: uuid.UUID, payload: SuperAdminTicketUpdate, admin: SuperAdminUser, db: DB
) -> SuperAdminTicketOut:
    row = (
        await db.execute(
            select(HelpTicket, Tenant.name, User.email)
            .join(Tenant, HelpTicket.tenant_id == Tenant.id)
            .join(User, HelpTicket.user_id == User.id)
            .where(HelpTicket.id == ticket_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    ticket, tenant_name, user_email = row

    ticket = crud.apply_updates(ticket, payload.model_dump(exclude_unset=True))
    ticket = await crud.save(db, ticket)
    return SuperAdminTicketOut(
        id=ticket.id, ticket_number=ticket.ticket_number, tenant_id=ticket.tenant_id,
        tenant_name=tenant_name, user_email=user_email, subject=ticket.subject,
        category=ticket.category, priority=ticket.priority, status=ticket.status,
        message=ticket.message, created_at=ticket.created_at, updated_at=ticket.updated_at,
    )


# =============================================================================
#  Leads
# =============================================================================


@router.get("/leads", response_model=Page[SuperAdminLeadOut])
async def list_leads(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    status_filter: str | None = None,
    limit: Annotated[int, "1-100"] = 50,
    offset: int = 0,
) -> dict:
    filters = []
    if q:
        filters.append(
            or_(Lead.email.ilike(f"%{q}%"), Lead.full_name.ilike(f"%{q}%"), Lead.shop_name.ilike(f"%{q}%"))
        )
    if status_filter:
        filters.append(Lead.status == status_filter)

    total = (await db.execute(select(func.count(Lead.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(Lead).where(*filters).order_by(Lead.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/leads/{lead_id}/convert",
    response_model=SuperAdminTenantOut,
    status_code=status.HTTP_201_CREATED,
)
async def convert_lead(
    lead_id: uuid.UUID, payload: SuperAdminConvertLeadIn, admin: SuperAdminUser, db: DB
) -> Tenant:
    """A lead who paid becomes a real customer — reuses their existing
    email and password hash from signup (crud.create_tenant_owner_and_site's
    password_hash param) rather than making them set a new password. The
    lead row itself is left as-is afterward, a historical record; trying to
    convert the same lead twice just 409s (their email is now taken)."""
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    user, _site = await crud.create_tenant_owner_and_site(
        db,
        email=lead.email,
        password_hash=lead.password_hash,
        workspace_name=payload.workspace_name,
        plan=payload.plan,
        template_key=payload.template_key,
        site_name=payload.site_name,
        subdomain=payload.subdomain,
        full_name=lead.full_name,
    )
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    return tenant


# =============================================================================
#  Users
# =============================================================================


@router.get("/users", response_model=Page[SuperAdminUserOut])
async def list_users(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: Annotated[int, "1-100"] = 50,
    offset: int = 0,
) -> dict:
    filters = []
    if q:
        filters.append(or_(User.email.ilike(f"%{q}%"), User.full_name.ilike(f"%{q}%")))
    if tenant_id:
        filters.append(User.tenant_id == tenant_id)

    total = (await db.execute(select(func.count(User.id)).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(User, Tenant.name)
            .join(Tenant, User.tenant_id == Tenant.id)
            .where(*filters)
            .order_by(User.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).all()
    items = [
        SuperAdminUserOut(
            id=u.id, tenant_id=u.tenant_id, tenant_name=tenant_name, email=u.email,
            full_name=u.full_name, role=u.role, is_active=u.is_active,
            is_superadmin=u.is_superadmin, last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u, tenant_name in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/users", response_model=SuperAdminUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: SuperAdminUserCreateIn, admin: SuperAdminUser, db: DB) -> SuperAdminUserOut:
    """A second login under an existing tenant — see
    SuperAdminUserCreateIn's docstring."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == payload.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    existing = await db.execute(select(User.id).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email exists.")

    user = User(
        tenant_id=tenant.id,
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    user = await crud.save(db, user)
    return SuperAdminUserOut(
        id=user.id, tenant_id=user.tenant_id, tenant_name=tenant.name, email=user.email,
        full_name=user.full_name, role=user.role, is_active=user.is_active,
        is_superadmin=user.is_superadmin, last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.patch("/users/{user_id}", response_model=SuperAdminUserOut)
async def update_user(
    user_id: uuid.UUID, payload: SuperAdminUserUpdate, admin: SuperAdminUser, db: DB
) -> SuperAdminUserOut:
    row = (
        await db.execute(
            select(User, Tenant.name).join(Tenant, User.tenant_id == Tenant.id)
            .where(User.id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user, tenant_name = row

    data = payload.model_dump(exclude_unset=True)
    new_password = data.pop("new_password", None)
    user = crud.apply_updates(user, data)
    if new_password:
        user.password_hash = hash_password(new_password)
    user = await crud.save(db, user)

    # A role/active/superadmin change or a forced password reset should take
    # effect immediately, not wait out the access token's own expiry — same
    # reasoning as /auth/change-password.
    await revoke_all_user_tokens(user.id)

    return SuperAdminUserOut(
        id=user.id, tenant_id=user.tenant_id, tenant_name=tenant_name, email=user.email,
        full_name=user.full_name, role=user.role, is_active=user.is_active,
        is_superadmin=user.is_superadmin, last_login_at=user.last_login_at,
        created_at=user.created_at,
    )
