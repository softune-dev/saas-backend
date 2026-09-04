"""Shared logic for turning an Order into a real Steadfast consignment —
used by both the dashboard's manual "book" action (app/api/commerce.py) and
the automatic on-checkout path (app/worker.py's handle_book_courier), so the
two paths can never silently drift apart.

Only Steadfast has a real booking flow today (Pathao/RedX/eCourier stay
connect-only) — see app/steadfast.py.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import courier_crypto, crud, steadfast
from app.models import CourierConnection, Order


def order_recipient_fields(order: Order) -> tuple[str, str, str] | None:
    """Returns (name, phone, address) from an order's free-form customer
    JSON, or None if there's no usable phone to book with."""
    customer = order.customer or {}
    name = " ".join(
        str(customer.get(k, "")).strip() for k in ("first_name", "last_name")
    ).strip() or "Customer"
    phone = crud.extract_customer_phone(customer)
    if not phone:
        return None
    address_parts = [
        str(customer.get(k, "")).strip()
        for k in ("address", "city")
        if str(customer.get(k, "")).strip()
    ]
    address = ", ".join(address_parts) or "No address provided"
    return name, phone, address


async def get_steadfast_connection(
    db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID
) -> CourierConnection | None:
    connections, _ = await crud.list_scoped(
        db, CourierConnection, tenant_id,
        filters=[
            CourierConnection.site_id == site_id,
            CourierConnection.provider == "steadfast",
        ],
        limit=1,
    )
    connection = connections[0] if connections else None
    if connection is None or connection.status != "connected":
        return None
    return connection


async def book_order(
    db: AsyncSession, order: Order, connection: CourierConnection
) -> tuple[bool, str | None]:
    """Books `order` with `connection`'s Steadfast account and writes the
    result onto the order. Returns (ok, error_message) — never raises; a
    courier outage or a bad address is an expected outcome the caller
    surfaces (as a 502 in the dashboard's manual flow, as a log line in the
    automatic one), not a crash either way.
    """
    if order.courier_consignment_id is not None:
        return False, "This order is already booked with a courier."

    fields = order_recipient_fields(order)
    if fields is None:
        return False, "This order has no phone number to book a courier with."
    name, phone, address = fields

    result, error = await steadfast.create_consignment(
        api_key=courier_crypto.decrypt(connection.api_key_encrypted),
        secret_key=courier_crypto.decrypt(connection.secret_key_encrypted),
        invoice=order.order_number,
        recipient_name=name,
        recipient_phone=phone,
        recipient_address=address,
        cod_amount_cents=order.total_cents,
        base_url=connection.base_url,
    )
    if error:
        return False, error

    order.courier_provider = "steadfast"
    order.courier_consignment_id = result["consignment_id"]
    order.courier_tracking_code = result["tracking_code"]
    order.delivery_status = result["status"]
    await crud.save(db, order)
    return True, None
