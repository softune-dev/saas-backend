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

from app import crud
from app.db import get_db
from app.models import Customer, Order, Site
from app.schemas import CustomerDetailOut, CustomerOut, CustomerUpdate, Page
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
    rows, total = await crud.list_scoped(
        db, Customer, user.tenant_id,
        filters=[Customer.site_id == site_id],
        order_by=Customer.created_at.desc(), limit=limit, offset=offset,
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

    return {
        **CustomerOut.model_validate(customer).model_dump(),
        "order_count": order_count,
        "total_spent_cents": total_spent,
        "last_order_at": last_order_at,
        "orders": orders,
    }


@router.patch("/sites/{site_id}/customers/{customer_id}", response_model=CustomerOut)
async def update_customer(
    site_id: uuid.UUID, customer_id: uuid.UUID, payload: CustomerUpdate, user: CurrentUser, db: DB
) -> Customer:
    await _owned_site(db, user.tenant_id, site_id)
    customer = await crud.get_scoped(db, Customer, user.tenant_id, customer_id)
    crud.apply_updates(customer, payload.model_dump(exclude_unset=True))
    return await crud.save(db, customer)
