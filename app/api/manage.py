"""Deleting the merchant's own records: orders, customers, abandoned
checkouts, inquiries, notifications and Softune Pay payments.

Everything here is tenant-scoped through crud.get_scoped, and a row from
another site or tenant answers 404, never 403 (CLAUDE.md rules 1 and 3).

What a delete takes with it is decided by the database's own foreign keys,
not by code here:
  - an order: its order_items go (ON DELETE CASCADE); a Softune Pay payment
    that matched it stays, unlinked (SET NULL)
  - a customer: their orders stay, unlinked (orders.customer_id SET NULL);
    a later order from the same phone starts a fresh customer record

Orders are the one delete with a side effect worth knowing: stock. An order
took its units out of stock when it was placed. Deleting an order that
never left the shop (anything except "fulfilled" or "refunded") puts those
units back, so a test or mistaken order doesn't leave stock short. A
fulfilled order's units really did leave, so they stay out.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud
from app.db import get_db
from app.models import (
    AbandonedCheckout,
    Customer,
    Inquiry,
    Notification,
    Order,
    OrderItem,
    PaySms,
    Product,
    Site,
)
from app.security import CurrentUser

router = APIRouter(tags=["manage"])
DB = Annotated[AsyncSession, Depends(get_db)]

# Statuses where the goods physically left the shop: deleting the order
# must not put them back in stock.
_SHIPPED = {"fulfilled", "refunded"}
_MAX_BULK = 200


class BulkIds(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=_MAX_BULK)


class DeletedOut(BaseModel):
    deleted: int


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def _restock(db: AsyncSession, orders: list[Order]) -> bool:
    """Put back the units of orders that never shipped. True if any product
    stock changed (the storefront cache then needs dropping)."""
    order_ids = [o.id for o in orders if o.status not in _SHIPPED]
    if not order_ids:
        return False
    lines = (
        await db.execute(
            select(OrderItem.product_id, OrderItem.quantity).where(
                OrderItem.order_id.in_(order_ids), OrderItem.product_id.is_not(None)
            )
        )
    ).all()
    per_product: dict[uuid.UUID, int] = {}
    for product_id, qty in lines:
        per_product[product_id] = per_product.get(product_id, 0) + qty
    if not per_product:
        return False
    products = (
        await db.execute(select(Product).where(Product.id.in_(per_product.keys())))
    ).scalars().all()
    changed = False
    for product in products:
        if product.track_stock:
            product.stock += per_product[product.id]
            changed = True
    return changed


async def _delete_orders(db: AsyncSession, site: Site, orders: list[Order]) -> int:
    restocked = await _restock(db, orders)
    for order in orders:
        await db.delete(order)
    await db.commit()
    await cache.invalidate_dashboard(str(site.id))
    if restocked:
        await cache.invalidate_site(site.subdomain, site.custom_domain)
    return len(orders)


# ---- Orders --------------------------------------------------------------


@router.delete("/sites/{site_id}/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(site_id: uuid.UUID, order_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    order = await crud.get_scoped(db, Order, user.tenant_id, order_id)
    if order.site_id != site.id:
        raise _not_found("Order")
    await _delete_orders(db, site, [order])


@router.post("/sites/{site_id}/orders/bulk-delete", response_model=DeletedOut)
async def bulk_delete_orders(
    site_id: uuid.UUID, payload: BulkIds, user: CurrentUser, db: DB
) -> DeletedOut:
    """Deletes the selected orders of this site. Ids from another site or
    tenant are simply not matched, never an error that confirms they exist."""
    site = await _owned_site(db, user.tenant_id, site_id)
    orders = (
        await db.execute(
            select(Order).where(
                Order.tenant_id == user.tenant_id,
                Order.site_id == site.id,
                Order.id.in_(payload.ids),
            )
        )
    ).scalars().all()
    return DeletedOut(deleted=await _delete_orders(db, site, list(orders)))


# ---- Customers -----------------------------------------------------------


@router.delete("/sites/{site_id}/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    site_id: uuid.UUID, customer_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    """Removes the customer record. Their orders stay (history), unlinked."""
    await _owned_site(db, user.tenant_id, site_id)
    customer = await crud.get_scoped(db, Customer, user.tenant_id, customer_id)
    if customer.site_id != site_id:
        raise _not_found("Customer")
    await crud.delete(db, customer)
    await cache.invalidate_dashboard(str(site_id))


@router.post("/sites/{site_id}/customers/bulk-delete", response_model=DeletedOut)
async def bulk_delete_customers(
    site_id: uuid.UUID, payload: BulkIds, user: CurrentUser, db: DB
) -> DeletedOut:
    await _owned_site(db, user.tenant_id, site_id)
    result = await db.execute(
        delete(Customer).where(
            Customer.tenant_id == user.tenant_id,
            Customer.site_id == site_id,
            Customer.id.in_(payload.ids),
        )
    )
    await db.commit()
    await cache.invalidate_dashboard(str(site_id))
    return DeletedOut(deleted=result.rowcount or 0)


# ---- Abandoned checkouts -------------------------------------------------


@router.delete(
    "/sites/{site_id}/abandoned-checkouts/{checkout_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_abandoned_checkout(
    site_id: uuid.UUID, checkout_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    row = await crud.get_scoped(db, AbandonedCheckout, user.tenant_id, checkout_id)
    if row.site_id != site_id:
        raise _not_found("Abandoned checkout")
    await crud.delete(db, row)


@router.delete("/sites/{site_id}/abandoned-checkouts", response_model=DeletedOut)
async def clear_abandoned_checkouts(site_id: uuid.UUID, user: CurrentUser, db: DB) -> DeletedOut:
    """Clears the whole list (the ones still shown: not later converted)."""
    await _owned_site(db, user.tenant_id, site_id)
    result = await db.execute(
        delete(AbandonedCheckout).where(
            AbandonedCheckout.tenant_id == user.tenant_id,
            AbandonedCheckout.site_id == site_id,
            AbandonedCheckout.converted_at.is_(None),
        )
    )
    await db.commit()
    return DeletedOut(deleted=result.rowcount or 0)


# ---- Inquiries -----------------------------------------------------------


@router.delete("/sites/{site_id}/inquiries/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inquiry(
    site_id: uuid.UUID, inquiry_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    row = await crud.get_scoped(db, Inquiry, user.tenant_id, inquiry_id)
    if row.site_id != site_id:
        raise _not_found("Inquiry")
    await crud.delete(db, row)
    await cache.invalidate_dashboard(str(site_id))


# ---- Notifications -------------------------------------------------------


@router.delete(
    "/sites/{site_id}/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_notification(
    site_id: uuid.UUID, notification_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    await _owned_site(db, user.tenant_id, site_id)
    row = await crud.get_scoped(db, Notification, user.tenant_id, notification_id)
    if row.site_id != site_id:
        raise _not_found("Notification")
    await crud.delete(db, row)


@router.delete("/sites/{site_id}/notifications", response_model=DeletedOut)
async def clear_notifications(site_id: uuid.UUID, user: CurrentUser, db: DB) -> DeletedOut:
    await _owned_site(db, user.tenant_id, site_id)
    result = await db.execute(
        delete(Notification).where(
            Notification.tenant_id == user.tenant_id, Notification.site_id == site_id
        )
    )
    await db.commit()
    return DeletedOut(deleted=result.rowcount or 0)


# ---- Softune Pay payments ------------------------------------------------


@router.delete("/sites/{site_id}/pay/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pay_payment(
    site_id: uuid.UUID, payment_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    """Removes a received-payment record (e.g. a test SMS). An order it
    already marked paid stays paid; this only drops the SMS record."""
    await _owned_site(db, user.tenant_id, site_id)
    row = await crud.get_scoped(db, PaySms, user.tenant_id, payment_id)
    if row.site_id != site_id:
        raise _not_found("Payment")
    await crud.delete(db, row)
