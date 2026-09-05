"""Read-only-ish customer records, derived from real orders.

There is no "add a customer" form — a Customer row is created implicitly the
first time a phone number places an order (see crud.get_or_create_customer,
called from both commerce.create_order and public.create_public_order).
Merchants can only rename/annotate one that already exists here.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, risk_score as risk_score_module
from app.db import get_db
from app.models import AbandonedCheckout, Customer, FraudIpBlocklistEntry, Order, Product, Site
from app.schemas import (
    AbandonedCheckoutItemOut,
    AbandonedCheckoutOut,
    CustomerDetailOut,
    CustomerOut,
    CustomerUpdate,
    Page,
)
from app.security import CurrentUser

router = APIRouter(tags=["customers"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


@router.get("/sites/{site_id}/customers", response_model=Page[CustomerOut])
async def list_customers(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)

    cache_key = cache.dashboard_key(str(site_id), "customers", f"{limit}:{offset}")
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return {
            "items": [CustomerOut(**row) for row in cached["items"]],
            "total": cached["total"], "limit": limit, "offset": offset,
        }

    rows, total = await crud.list_scoped(
        db, Customer, user.tenant_id,
        filters=[Customer.site_id == site_id],
        order_by=Customer.created_at.desc(), limit=limit, offset=offset,
    )
    await cache.set_json(
        cache_key,
        {"items": [CustomerOut.model_validate(r).model_dump(mode="json") for r in rows], "total": total},
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/sites/{site_id}/customers/{customer_id}", response_model=CustomerDetailOut)
async def get_customer(
    site_id: uuid.UUID, customer_id: uuid.UUID, user: CurrentUser, db: DB
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)
    customer = await crud.get_scoped(db, Customer, user.tenant_id, customer_id)

    # Only orders linked to this customer record — orders placed before this
    # feature shipped keep their JSONB snapshot but were never linked, so
    # these totals reflect linked history, not necessarily every order this
    # phone number has ever placed. See migrations/032_customers.sql.
    orders, order_count = await crud.list_scoped(
        db, Order, user.tenant_id,
        filters=[Order.customer_id == customer_id],
        order_by=Order.created_at.desc(), limit=100, offset=0,
    )
    total_spent = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_cents), 0)).where(
                Order.customer_id == customer_id, Order.tenant_id == user.tenant_id
            )
        )
    ).scalar_one()
    last_order_at = orders[0].created_at if orders else None

    # Risk score inputs — see app/risk_score.py's own docstring for why each
    # of these traces back to a real column/table instead of an inference.
    latest_ip = next((o.ip_address for o in orders if o.ip_address), None)
    ip_blocklisted = False
    if latest_ip:
        ip_blocklisted = (
            await db.execute(
                select(FraudIpBlocklistEntry.id).where(
                    FraudIpBlocklistEntry.site_id == site_id,
                    FraudIpBlocklistEntry.ip_address == latest_ip,
                ).limit(1)
            )
        ).scalar_one_or_none() is not None
    open_order_count = sum(1 for o in orders if o.status in ("pending", "paid"))
    latest_device_id = next((o.device_id for o in orders if o.device_id), None)

    risk = risk_score_module.compute_risk_score(
        orders=orders,
        current_device_id=latest_device_id,
        ip_blocklisted=ip_blocklisted,
        has_open_duplicate=open_order_count > 1,
    )

    return {
        **CustomerOut.model_validate(customer).model_dump(),
        "order_count": order_count,
        "total_spent_cents": total_spent,
        "last_order_at": last_order_at,
        "orders": orders,
        "risk_score": risk,
    }


@router.patch("/sites/{site_id}/customers/{customer_id}", response_model=CustomerOut)
async def update_customer(
    site_id: uuid.UUID, customer_id: uuid.UUID, payload: CustomerUpdate, user: CurrentUser, db: DB
) -> Customer:
    await _owned_site(db, user.tenant_id, site_id)
    customer = await crud.get_scoped(db, Customer, user.tenant_id, customer_id)
    crud.apply_updates(customer, payload.model_dump(exclude_unset=True))
    customer = await crud.save(db, customer)
    await cache.invalidate_dashboard(str(site_id))
    return customer


@router.get(
    "/sites/{site_id}/abandoned-checkouts", response_model=Page[AbandonedCheckoutOut]
)
async def list_abandoned_checkouts(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Shoppers who entered a phone during checkout but never completed the
    order — see AbandonedCheckout's own docstring. Excludes ones that later
    converted (converted_at set by create_public_order). Data only, no
    automated follow-up — a merchant reaches out themselves."""
    await _owned_site(db, user.tenant_id, site_id)

    rows, total = await crud.list_scoped(
        db, AbandonedCheckout, user.tenant_id,
        filters=[
            AbandonedCheckout.site_id == site_id,
            AbandonedCheckout.converted_at.is_(None),
        ],
        order_by=AbandonedCheckout.updated_at.desc(), limit=limit, offset=offset,
    )

    product_ids = {
        item["product_id"]
        for row in rows
        for item in row.items
        if item.get("product_id")
    }
    names: dict[str, str] = {}
    if product_ids:
        product_rows = (
            await db.execute(
                select(Product.id, Product.name).where(Product.id.in_(product_ids))
            )
        ).all()
        names = {str(pid): name for pid, name in product_rows}

    items_out = [
        {
            "id": row.id,
            "phone": row.phone,
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": names.get(str(item["product_id"]), "Removed product"),
                    "quantity": item["quantity"],
                }
                for item in row.items
            ],
            "subtotal_cents": row.subtotal_cents,
            "converted_at": row.converted_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]
    return {"items": items_out, "total": total, "limit": limit, "offset": offset}
