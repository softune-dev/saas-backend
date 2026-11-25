"""The operator's view of the Free plan: customers split into Free, Paid and
Internal, how close each Free store is to its limits and to deletion, the
headline numbers, and the record kept of every deleted account.

Read-only and cross-tenant, gated by SuperAdminUser like the rest of
/superadmin (see app/api/superadmin.py for why rule 1 does not apply here).
Storage is left out of the list on purpose: it is counted from Cloudinary, one
slow call per store, and the tenant details already show it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import contacts, order_limit, plans
from app.db import get_db
from app.inactivity import inactive_limit
from app.models import DeletedAccount, PaymentClaim, Product, Site, Tenant, TenantOrderMonth, User
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin", tags=["superadmin-plans"])
DB = Annotated[AsyncSession, Depends(get_db)]

Group = Literal["free", "paid", "internal"]

# "Near a limit" starts at 80% of Free's products or orders.
NEAR = 0.8
# "About to be deleted": the last week before an inactive Free store goes.
DELETION_SOON_DAYS = 7


def _group_filter(group: Group):
    if group == "free":
        return Tenant.plan == "free"
    if group == "paid":
        return Tenant.plan.in_(plans.PAID_PLANS)
    # Demo showcase stores, RIVELLE and anything else nobody pays for.
    return and_(Tenant.plan != "free", Tenant.plan.notin_(plans.PAID_PLANS))


def _owner_is_staff():
    return exists().where(User.tenant_id == Tenant.id, User.is_superadmin.is_(True))


class PlanTenantOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    status: str
    created_at: datetime
    last_active_at: datetime | None
    subdomain: str | None
    products: int
    product_limit: int | None
    orders_this_month: int
    order_limit: int | None
    blocked_this_month: int
    # Free only: when the inactivity job would delete it if nothing changes.
    # Null for paid, internal, suspended and staff accounts, which it never
    # deletes.
    deletes_at: datetime | None


class PlanTenantPage(Page[PlanTenantOut]):
    counts: dict[str, int]


def _deletes_at(t: Tenant, paid_before: bool, staff: bool) -> datetime | None:
    if t.plan != "free" or t.status != "active" or staff or t.last_active_at is None:
        return None
    return t.last_active_at + timedelta(days=inactive_limit(paid_before))


@router.get("/plan-tenants", response_model=PlanTenantPage)
async def plan_tenants(
    admin: SuperAdminUser,
    db: DB,
    group: Group = "free",
    q: str | None = None,
    sort: Literal["newest", "last_active", "deletes_soon"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = [_group_filter(group)]
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                Tenant.name.ilike(like),
                Tenant.slug.ilike(like),
                exists().where(Site.tenant_id == Tenant.id, Site.subdomain.ilike(like)),
                exists().where(User.tenant_id == Tenant.id, User.email.ilike(like)),
            )
        )

    counts = {
        g: (await db.execute(select(func.count(Tenant.id)).where(_group_filter(g)))).scalar_one()
        for g in ("free", "paid", "internal")
    }
    total = (await db.execute(select(func.count(Tenant.id)).where(*filters))).scalar_one()

    order = {
        "newest": Tenant.created_at.desc(),
        "last_active": Tenant.last_active_at.desc().nulls_last(),
        # Oldest activity first: those are the next to go.
        "deletes_soon": Tenant.last_active_at.asc().nulls_last(),
    }[sort]
    rows = (
        await db.execute(
            select(Tenant, _owner_is_staff()).where(*filters).order_by(order, Tenant.id).limit(limit).offset(offset)
        )
    ).all()
    ids = [t.id for t, _ in rows]

    products = dict(
        (await db.execute(
            select(Product.tenant_id, func.count()).where(Product.tenant_id.in_(ids)).group_by(Product.tenant_id)
        )).all()
    ) if ids else {}
    months = {
        tid: (o, b)
        for tid, o, b in (await db.execute(
            select(TenantOrderMonth.tenant_id, TenantOrderMonth.orders, TenantOrderMonth.blocked).where(
                TenantOrderMonth.tenant_id.in_(ids), TenantOrderMonth.month == order_limit.current_month()
            )
        )).all()
    } if ids else {}
    subdomains: dict[uuid.UUID, str] = {}
    if ids:
        for tid, sub in (await db.execute(
            select(Site.tenant_id, Site.subdomain).where(Site.tenant_id.in_(ids)).order_by(Site.created_at)
        )).all():
            subdomains.setdefault(tid, sub)
    paid = await contacts.paid_before(db, ids) if ids else set()

    items = []
    for t, staff in rows:
        plan = plans.get_plan(t.plan)
        orders, blocked = months.get(t.id, (0, 0))
        items.append({
            "id": t.id, "name": t.name, "slug": t.slug, "plan": t.plan, "status": t.status,
            "created_at": t.created_at, "last_active_at": t.last_active_at,
            "subdomain": subdomains.get(t.id),
            "products": products.get(t.id, 0), "product_limit": plan.products,
            "orders_this_month": orders, "order_limit": plan.orders_per_month,
            "blocked_this_month": blocked,
            "deletes_at": _deletes_at(t, t.id in paid, staff),
        })
    return {"items": items, "total": total, "limit": limit, "offset": offset, "counts": counts}


class PlanBrief(ORMModel):
    id: uuid.UUID
    name: str
    plan: str
    at: datetime | None
    detail: str


class PlanOverviewOut(ORMModel):
    counts: dict[str, int]
    new_this_week: dict[str, int]
    upgraded_from_free_30d: int
    near_limit: list[PlanBrief]
    deleting_soon: list[PlanBrief]
    recent_upgrades: list[PlanBrief]


@router.get("/plan-overview", response_model=PlanOverviewOut)
async def plan_overview(admin: SuperAdminUser, db: DB) -> dict:
    now = datetime.now(UTC)
    week = now - timedelta(days=7)

    counts, new = {}, {}
    for g in ("free", "paid", "internal"):
        counts[g] = (await db.execute(select(func.count(Tenant.id)).where(_group_filter(g)))).scalar_one()
        new[g] = (await db.execute(
            select(func.count(Tenant.id)).where(_group_filter(g), Tenant.created_at >= week)
        )).scalar_one()

    # Near a limit: Free stores at 80% of their products or this month's orders.
    free = plans.PLANS["free"]
    product_count = (
        select(func.count()).select_from(Product).where(Product.tenant_id == Tenant.id).scalar_subquery()
    )
    month_orders = func.coalesce(
        select(TenantOrderMonth.orders).where(
            TenantOrderMonth.tenant_id == Tenant.id, TenantOrderMonth.month == order_limit.current_month()
        ).scalar_subquery(),
        0,
    )
    near_rows = (await db.execute(
        select(Tenant, product_count, month_orders)
        .where(
            Tenant.plan == "free",
            or_(product_count >= int(free.products * NEAR), month_orders >= int(free.orders_per_month * NEAR)),
        )
        .order_by(month_orders.desc(), product_count.desc())
        .limit(20)
    )).all()
    near = [
        {"id": t.id, "name": t.name, "plan": t.plan, "at": t.last_active_at,
         "detail": f"{p}/{free.products} products, {o}/{free.orders_per_month} orders"}
        for t, p, o in near_rows
    ]

    # Deleting soon: the same rule the job uses, one week ahead. The shorter
    # Free period is checked in SQL; a former customer's longer one in Python.
    candidates = (await db.execute(
        select(Tenant).where(
            Tenant.plan == "free", Tenant.status == "active", ~_owner_is_staff(),
            Tenant.last_active_at <= now - timedelta(days=plans.FREE_INACTIVE_DAYS - DELETION_SOON_DAYS),
        ).order_by(Tenant.last_active_at).limit(200)
    )).scalars().all()
    paid = await contacts.paid_before(db, [t.id for t in candidates]) if candidates else set()
    soon = []
    for t in candidates:
        at = t.last_active_at + timedelta(days=inactive_limit(t.id in paid))
        if at - now <= timedelta(days=DELETION_SOON_DAYS):
            soon.append({"id": t.id, "name": t.name, "plan": t.plan, "at": at, "detail": "former customer" if t.id in paid else ""})
    soon = soon[:20]

    # Every sign-up starts on Free, so a store's first verified plan payment
    # is an upgrade from Free.
    first_paid = (
        select(PaymentClaim.tenant_id, func.min(PaymentClaim.verified_at).label("at"))
        .where(PaymentClaim.kind == "plan", PaymentClaim.status == "verified", PaymentClaim.verified_at.isnot(None))
        .group_by(PaymentClaim.tenant_id)
        .subquery()
    )
    upgraded = (await db.execute(
        select(func.count()).select_from(first_paid).where(first_paid.c.at >= now - timedelta(days=30))
    )).scalar_one()
    recent = (await db.execute(
        select(Tenant, first_paid.c.at).join(first_paid, first_paid.c.tenant_id == Tenant.id)
        .order_by(first_paid.c.at.desc()).limit(10)
    )).all()

    return {
        "counts": counts,
        "new_this_week": new,
        "upgraded_from_free_30d": upgraded,
        "near_limit": near,
        "deleting_soon": soon,
        "recent_upgrades": [
            {"id": t.id, "name": t.name, "plan": t.plan, "at": at, "detail": plans.get_plan(t.plan).name}
            for t, at in recent
        ],
    }


class DeletedAccountOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    reason: str
    email: str | None
    full_name: str | None
    phone: str | None
    shop_name: str | None
    subdomain: str | None
    custom_domain: str | None
    plan: str | None
    ever_paid: bool
    deleted_at: datetime


class DeletedAccountDetailOut(DeletedAccountOut):
    snapshot: dict


@router.get("/deleted-accounts", response_model=Page[DeletedAccountOut])
async def deleted_accounts(
    admin: SuperAdminUser,
    db: DB,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(
            DeletedAccount.email.ilike(like), DeletedAccount.phone.ilike(like),
            DeletedAccount.shop_name.ilike(like), DeletedAccount.subdomain.ilike(like),
            DeletedAccount.full_name.ilike(like),
        ))
    total = (await db.execute(select(func.count(DeletedAccount.id)).where(*filters))).scalar_one()
    rows = (await db.execute(
        select(DeletedAccount).where(*filters).order_by(DeletedAccount.deleted_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/deleted-accounts/{record_id}", response_model=DeletedAccountDetailOut)
async def deleted_account(record_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> DeletedAccount:
    row = await db.get(DeletedAccount, record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    return row
