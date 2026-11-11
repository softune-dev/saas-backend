"""Softune Pay endpoints: the merchant's phone confirms manual wallet payments.

Two audiences, two routers:

- admin_router (/sites/{site_id}/pay/...): the dashboard, with the merchant's
  normal login. Makes a pairing code (shown as a QR), lists and removes phones,
  lists what came in.
- device_router (/pay/...): the Softune Pay Android app. It has no user login;
  it carries the secret token it got when paired, as "Authorization: Bearer".
  Only the token's SHA-256 is stored (pay_devices.token_hash).

Pairing: the dashboard asks for a code; we keep {code -> site} in Redis for 10
minutes; the phone sends the code (typed, or read from the QR) and gets its
token once. A code works once.

Parsing and matching live in app/pay_matching.py. See migrations/082.
"""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, pay_matching
from app.config import settings
from app.db import get_db
from app.models import Order, PayDevice, PaySms, Product, Site, User
from app.ratelimit import rate_limit
from app.security import CurrentUser

DB = Annotated[AsyncSession, Depends(get_db)]

admin_router = APIRouter(prefix="/sites/{site_id}/pay", tags=["softune-pay"])
device_router = APIRouter(prefix="/pay", tags=["softune-pay-device"])

_CODE_TTL = 600
# No 0/O/1/I/L: the code is read off a screen and typed on a phone.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# Bangladesh time, for "today" totals. No DST, so a fixed offset is exact.
_BD = timedelta(hours=6)
_SEEN_EVERY = timedelta(seconds=60)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pair_key(code: str) -> str:
    return f"pay-pair:{code.upper()}"


def _unavailable(exc: Exception | None = None) -> HTTPException:
    # Pairing needs Redis; there is no safe fallback, so say so plainly.
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Pairing is unavailable right now. Please try again in a minute.")


# =============================================================================
#  Shared: how a payment looks to the app and the dashboard
# =============================================================================

def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _order_out(order: Order, images: dict[uuid.UUID, str], orders_before: dict[uuid.UUID, int]) -> dict:
    c = order.customer or {}
    meta = order.meta or {}
    name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("name") or "Customer"
    return {
        "id": str(order.id),
        "number": order.order_number,
        "status": order.status,
        "placed_at": _iso(order.created_at),
        # Line items come from the order's own snapshots (CLAUDE.md rule 8).
        # The image is the live product's first photo, used only as a
        # thumbnail; nothing about price or name is read from live products.
        "items": [
            {
                "name": it.name_snapshot,
                "variant": it.variant_snapshot,
                "qty": it.quantity,
                "unit_cents": it.unit_price_cents,
                "total_cents": it.total_cents,
                "image_url": images.get(it.product_id) if it.product_id else None,
            }
            for it in order.items
        ],
        "subtotal_cents": order.subtotal_cents,
        "shipping_cents": order.shipping_cents,
        "discount_cents": max(0, order.subtotal_cents + order.shipping_cents + order.tax_cents - order.total_cents),
        "total_cents": order.total_cents,
        "currency": order.currency,
        "delivery_zone": (meta.get("delivery_location") or {}).get("name") if isinstance(meta.get("delivery_location"), dict) else meta.get("delivery_location"),
        "note": order.notes,
        "customer": {
            "name": name,
            "phone": pay_matching.mask_number(c.get("phone")),
            "address": c.get("address"),
            "area": c.get("city"),
            "orders_before": orders_before.get(order.customer_id, 0) if order.customer_id else 0,
        },
    }


async def _context(db: AsyncSession, orders: list[Order]) -> tuple[dict, dict]:
    """Product thumbnails and each customer's earlier-order count, in two
    batched queries for the whole list (not one per payment)."""
    product_ids = {it.product_id for o in orders for it in o.items if it.product_id}
    images: dict[uuid.UUID, str] = {}
    if product_ids:
        for pid, imgs in (await db.execute(select(Product.id, Product.images).where(Product.id.in_(product_ids)))).all():
            url = next((i.get("url") for i in (imgs or []) if isinstance(i, dict) and i.get("url")), None)
            if url:
                images[pid] = url
    customer_ids = {o.customer_id for o in orders if o.customer_id}
    before: dict[uuid.UUID, int] = {}
    if customer_ids:
        rows = await db.execute(
            select(Order.customer_id, func.count(Order.id)).where(Order.customer_id.in_(customer_ids)).group_by(Order.customer_id)
        )
        before = {cid: max(0, n - 1) for cid, n in rows.all()}
    return images, before


async def _payments(db: AsyncSession, site: Site, limit: int, status_filter: str | None) -> list[dict]:
    """Everything money-related for one store, newest first: SMS that came in
    (confirmed / no order / amount differs) plus manual orders still waiting
    for their SMS."""
    items: list[tuple[datetime, dict, Order | None]] = []

    if status_filter in (None, "matched", "unmatched", "mismatch"):
        q = select(PaySms).where(PaySms.site_id == site.id, PaySms.tenant_id == site.tenant_id)
        if status_filter:
            q = q.where(PaySms.status == status_filter)
        sms_rows = (await db.execute(q.order_by(PaySms.created_at.desc()).limit(limit))).scalars().all()
        order_ids = [s.order_id for s in sms_rows if s.order_id]
        orders = {}
        if order_ids:
            orders = {o.id: o for o in (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()}
        for s in sms_rows:
            items.append((s.sms_at, {
                "id": str(s.id),
                "kind": "sms",
                "status": s.status,
                "provider": s.provider,
                "amount_cents": s.amount_cents,
                "trx_id": s.trx_id,
                "sender": pay_matching.mask_number(s.sender_number),
                "at": _iso(s.sms_at),
                "raw_text": s.raw_text,
            }, orders.get(s.order_id) if s.order_id else None))

    if status_filter in (None, "waiting"):
        # Manual orders whose shopper says they paid, but no SMS yet.
        waiting = (await db.execute(
            select(Order).where(
                Order.site_id == site.id,
                Order.tenant_id == site.tenant_id,
                Order.status == "pending",
                Order.meta["payment_method"].astext == "manual",
                Order.meta["transaction_id"].astext != "",
            ).order_by(Order.created_at.desc()).limit(limit)
        )).scalars().all()
        for o in waiting:
            items.append((o.created_at, {
                "id": f"order-{o.id}",
                "kind": "order",
                "status": "waiting",
                "provider": None,
                "amount_cents": o.total_cents,
                "trx_id": (o.meta or {}).get("transaction_id"),
                "sender": None,
                "at": _iso(o.created_at),
                "raw_text": None,
            }, o))

    items.sort(key=lambda x: x[0], reverse=True)
    items = items[:limit]
    images, before = await _context(db, [o for _, _, o in items if o])
    return [{**row, "order": _order_out(o, images, before) if o else None} for _, row, o in items]


async def _summary(db: AsyncSession, site: Site) -> dict:
    now_bd = datetime.now(UTC) + _BD
    start_today = datetime(now_bd.year, now_bd.month, now_bd.day, tzinfo=UTC) - _BD
    start_yday = start_today - timedelta(days=1)
    start_week = start_today - timedelta(days=6)

    async def total(since: datetime, until: datetime | None = None, provider: str | None = None) -> tuple[int, int]:
        q = select(func.coalesce(func.sum(PaySms.amount_cents), 0), func.count(PaySms.id)).where(
            PaySms.site_id == site.id, PaySms.tenant_id == site.tenant_id,
            PaySms.status == "matched", PaySms.sms_at >= since,
        )
        if until:
            q = q.where(PaySms.sms_at < until)
        if provider:
            q = q.where(PaySms.provider == provider)
        cents, n = (await db.execute(q)).one()
        return int(cents), int(n)

    today_cents, today_n = await total(start_today)
    yday_cents, _ = await total(start_yday, start_today)
    week_cents, _ = await total(start_week)
    providers = {}
    for provider in ("bkash", "nagad", "rocket"):
        today, confirmed = await total(start_today, provider=provider)
        yesterday, _ = await total(start_yday, start_today, provider)
        week, _ = await total(start_week, provider=provider)
        providers[provider] = {
            "today_cents": today, "today_confirmed": confirmed,
            "yesterday_cents": yesterday, "week_cents": week,
        }
    to_look = (await db.execute(
        select(func.count(PaySms.id)).where(
            PaySms.site_id == site.id, PaySms.tenant_id == site.tenant_id,
            PaySms.status.in_(["unmatched", "mismatch"]), PaySms.created_at >= start_week,
        )
    )).scalar_one()
    waiting = (await db.execute(
        select(func.count(Order.id)).where(
            Order.site_id == site.id, Order.tenant_id == site.tenant_id, Order.status == "pending",
            Order.meta["payment_method"].astext == "manual", Order.meta["transaction_id"].astext != "",
        )
    )).scalar_one()
    return {
        "today_cents": today_cents,
        "today_confirmed": today_n,
        "yesterday_cents": yday_cents,
        "week_cents": week_cents,
        "to_look_at": int(to_look) + int(waiting),
        "providers": providers,
    }


# =============================================================================
#  Dashboard (merchant login)
# =============================================================================

def _device_out(d: PayDevice) -> dict:
    return {
        "id": str(d.id),
        "name": d.name,
        "app_version": d.app_version,
        "sim_carrier": d.sim_carrier,
        "sim_number": d.sim_number,
        "providers": d.providers,
        "watching": d.watching,
        "last_seen_at": _iso(d.last_seen_at),
        "created_at": _iso(d.created_at),
    }


@admin_router.post("/pairing-code", dependencies=[Depends(rate_limit("pay-code", limit=20, window_seconds=600))])
async def create_pairing_code(site_id: uuid.UUID, user: CurrentUser, db: DB) -> dict:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    try:
        await cache.client().set(
            _pair_key(code), json.dumps({"tenant_id": str(site.tenant_id), "site_id": str(site.id)}), ex=_CODE_TTL
        )
    except Exception as exc:  # noqa: BLE001 - Redis down: see _unavailable
        raise _unavailable(exc) from exc
    return {
        "code": code,
        "expires_at": _iso(datetime.now(UTC) + timedelta(seconds=_CODE_TTL)),
        # What the QR encodes. The app reads the code and which server to use.
        "qr": f"softunepay://pair?code={code}&api={settings.api_base_url}",
    }


@admin_router.get("/devices")
async def list_devices(site_id: uuid.UUID, user: CurrentUser, db: DB) -> list[dict]:
    await crud.get_scoped(db, Site, user.tenant_id, site_id)
    rows, _ = await crud.list_scoped(
        db, PayDevice, user.tenant_id,
        filters=[PayDevice.site_id == site_id, PayDevice.revoked_at.is_(None)],
        order_by=PayDevice.created_at.desc(), limit=20,
    )
    return [_device_out(d) for d in rows]


@admin_router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(site_id: uuid.UUID, device_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    await crud.get_scoped(db, Site, user.tenant_id, site_id)
    device = await crud.get_scoped(db, PayDevice, user.tenant_id, device_id)
    if device.site_id != site_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PayDevice not found")
    device.revoked_at = datetime.now(UTC)  # its token stops working at once
    await crud.save(db, device)


@admin_router.get("/payments")
async def admin_payments(
    site_id: uuid.UUID, user: CurrentUser, db: DB,
    limit: int = Query(50, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status", pattern="^(matched|unmatched|mismatch|waiting)$"),
) -> dict:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    return {"items": await _payments(db, site, limit, status_filter), "summary": await _summary(db, site)}


# =============================================================================
#  Phone (device token)
# =============================================================================

async def current_device(db: DB, authorization: Annotated[str | None, Header()] = None) -> PayDevice:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing device token")
    device = (await db.execute(select(PayDevice).where(PayDevice.token_hash == _hash(token)))).scalar_one_or_none()
    if device is None or device.revoked_at is not None:
        # 401 tells the app to show "disconnected, pair again".
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This phone is no longer connected")
    now = datetime.now(UTC)
    # Record that the phone is alive, but at most once a minute: the dashboard
    # only needs "last seen", not a write on every request.
    if device.last_seen_at is None or now - device.last_seen_at > _SEEN_EVERY:
        device.last_seen_at = now
        await crud.save(db, device)
    return device


Device = Annotated[PayDevice, Depends(current_device)]


async def _device_site(db: AsyncSession, device: PayDevice) -> Site:
    return await crud.get_scoped(db, Site, device.tenant_id, device.site_id)


async def _store_out(db: AsyncSession, site: Site) -> dict:
    owner = (await db.execute(
        select(User).where(User.tenant_id == site.tenant_id).order_by(User.role != "owner", User.created_at).limit(1)
    )).scalar_one_or_none()
    business = site.business or {}
    name = (owner.full_name if owner else None) or (owner.email.split("@")[0] if owner else "Owner")
    return {
        "name": site.name,
        "domain": site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}",
        "logo_url": business.get("logo_url"),
        "owner": {
            "name": name,
            "initials": "".join(p[0] for p in name.split()[:2]).upper() or "S",
            "role": owner.role if owner else "owner",
            "phone": pay_matching.mask_number(owner.phone) if owner and owner.phone else None,
            "email": owner.email if owner else None,
            "avatar_url": owner.avatar_url if owner else None,
        },
    }


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(default="Android phone", max_length=80)
    app_version: str | None = Field(default=None, max_length=20)


@device_router.post("/pair", dependencies=[Depends(rate_limit("pay-pair", limit=10, window_seconds=600))])
async def pair(payload: PairIn, db: DB) -> dict:
    key = _pair_key(payload.code.strip())
    try:
        c = cache.client()
        raw = await c.get(key)
        if raw:
            await c.delete(key)  # single use
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This code is wrong or has expired. Make a new one in the dashboard.")
    ref = json.loads(raw)
    tenant_id = uuid.UUID(ref["tenant_id"])
    # tenant_id comes from the verified code's own record, never from the phone.
    site = await crud.get_scoped(db, Site, tenant_id, uuid.UUID(ref["site_id"]))
    token = secrets.token_urlsafe(32)
    device = await crud.save(db, PayDevice(
        tenant_id=site.tenant_id, site_id=site.id, name=payload.device_name,
        token_hash=_hash(token), app_version=payload.app_version, last_seen_at=datetime.now(UTC),
    ))
    return {"token": token, "device_id": str(device.id), "store": await _store_out(db, site)}


@device_router.get("/me")
async def me(device: Device, db: DB) -> dict:
    site = await _device_site(db, device)
    return {"device": _device_out(device), "store": await _store_out(db, site)}


class HeartbeatIn(BaseModel):
    app_version: str | None = Field(default=None, max_length=20)
    sim_carrier: str | None = Field(default=None, max_length=40)
    sim_number: str | None = Field(default=None, max_length=20)
    providers: list[str] | None = Field(default=None, max_length=3)
    watching: bool | None = None


@device_router.post("/heartbeat")
async def heartbeat(payload: HeartbeatIn, device: Device, db: DB) -> dict:
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "providers" and value is not None:
            value = [p for p in value if p in pay_matching.SENDERS]
        setattr(device, field, value)
    device.last_seen_at = datetime.now(UTC)
    await crud.save(db, device)
    return {"ok": True}


class SmsIn(BaseModel):
    sender: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=1000)
    # When the phone received it (epoch ms). Falls back to now.
    received_at: int | None = None


@device_router.post("/sms", dependencies=[Depends(rate_limit("pay-sms", limit=120, window_seconds=60))])
async def receive_sms(payload: SmsIn, device: Device, db: DB) -> dict:
    """One SMS from the phone. 'ignored' for anything that isn't a wallet's
    money-in message; the app may send a little more than it needs to."""
    parsed = pay_matching.parse_sms(payload.sender, payload.body)
    if parsed is None:
        return {"result": "ignored"}
    if parsed.provider not in (device.providers or []):
        return {"result": "ignored", "reason": f"{parsed.provider} is switched off on this phone"}
    site = await _device_site(db, device)

    existing = (await db.execute(select(PaySms).where(
        PaySms.site_id == site.id, PaySms.provider == parsed.provider, PaySms.trx_id == parsed.trx_id,
    ))).scalar_one_or_none()
    if existing is not None:
        # The phone resends after a network blip; that's fine, count it once.
        return {"result": "duplicate", "status": existing.status}

    sms_at = datetime.now(UTC)
    if payload.received_at:
        candidate = datetime.fromtimestamp(payload.received_at / 1000, tz=UTC)
        if abs((candidate - sms_at).total_seconds()) < 7 * 86400:  # ignore nonsense clocks
            sms_at = candidate
    sms = await crud.save(db, PaySms(
        tenant_id=site.tenant_id, site_id=site.id, device_id=device.id,
        provider=parsed.provider, trx_id=parsed.trx_id, amount_cents=parsed.amount_cents,
        sender_number=parsed.sender_number, raw_text=payload.body, sms_at=sms_at,
    ))
    await pay_matching.match_sms(db, sms)
    return {"result": "saved", "status": sms.status, "order_id": str(sms.order_id) if sms.order_id else None}


@device_router.get("/payments")
async def device_payments(
    device: Device, db: DB,
    limit: int = Query(50, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status", pattern="^(matched|unmatched|mismatch|waiting)$"),
) -> dict:
    site = await _device_site(db, device)
    return {"items": await _payments(db, site, limit, status_filter)}


@device_router.get("/summary")
async def device_summary(device: Device, db: DB) -> dict:
    return await _summary(db, await _device_site(db, device))


@device_router.post("/unpair", status_code=status.HTTP_204_NO_CONTENT)
async def unpair(device: Device, db: DB) -> None:
    device.revoked_at = datetime.now(UTC)
    await crud.save(db, device)
