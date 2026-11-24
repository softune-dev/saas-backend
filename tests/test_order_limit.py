"""50 storefront orders a month on Free (app/order_limit.py).

The counter row is set directly for the test's own tenant instead of placing 49
orders. Queue, cache and rate limiter are faked: nothing here needs Redis or
RabbitMQ, and no real email, push or Meta event can be sent.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError

from app import cache, crud, order_limit, queue
from app.db import SessionLocal
from app.models import (
    Customer,
    Notification,
    Order,
    OrderCounter,
    Product,
    Site,
    TenantOrderMonth,
)

_ips = iter(range(1, 65000))


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[tuple[str, dict]]:
    """Every job the request tried to queue; nothing is really sent."""
    sent: list[tuple[str, dict]] = []

    async def spy(job, payload=None, *a, **k):
        sent.append((job, payload))
        return True

    monkeypatch.setattr(queue, "publish", spy)
    return sent


@pytest.fixture(autouse=True)
def store(monkeypatch) -> dict[str, tuple[dict, int | None]]:
    """An in-memory stand-in for Redis: key -> (value, ttl)."""
    kept: dict[str, tuple[dict, int | None]] = {}

    async def get_json(key):
        return kept[key][0] if key in kept else None

    async def set_json(key, value, ttl=None):
        kept[key] = (value, ttl)

    async def drop(*keys):
        for key in keys:
            kept.pop(key, None)

    def no_redis():
        raise ConnectionError("no redis in tests")

    monkeypatch.setattr(cache, "get_json", get_json)
    monkeypatch.setattr(cache, "set_json", set_json)
    monkeypatch.setattr(cache, "drop", drop)
    monkeypatch.setattr(cache, "client", no_redis)
    return kept


async def _set_counter(tenant_id, orders: int, *, month: date | None = None) -> None:
    async with SessionLocal() as db:
        values = {"tenant_id": uuid.UUID(tenant_id), "month": month or order_limit.current_month()}
        await db.execute(
            insert(TenantOrderMonth)
            .values(**values, orders=orders)
            .on_conflict_do_update(index_elements=["tenant_id", "month"], set_={"orders": orders})
        )
        await db.commit()


async def _counter(tenant_id, month: date | None = None) -> tuple[int, int] | None:
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(TenantOrderMonth.orders, TenantOrderMonth.blocked).where(
                    TenantOrderMonth.tenant_id == uuid.UUID(tenant_id),
                    TenantOrderMonth.month == (month or order_limit.current_month()),
                )
            )
        ).one_or_none()
        return tuple(row) if row else None


async def _count(model, tenant_id) -> int:
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(func.count()).select_from(model).where(model.tenant_id == uuid.UUID(tenant_id))
            )
        ).scalar_one()


async def _limit_notes(tenant_id) -> list[str]:
    async with SessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(Notification.title)
                    .where(
                        Notification.tenant_id == uuid.UUID(tenant_id),
                        Notification.type == "order_limit",
                    )
                    .order_by(Notification.created_at)
                )
            ).scalars()
        )


async def _stock(product_id) -> int:
    async with SessionLocal() as db:
        return (
            await db.execute(select(Product.stock).where(Product.id == uuid.UUID(product_id)))
        ).scalar_one()


async def _open_store(acct, *, whatsapp: str | None = "01712-345678", **product) -> str:
    """Publish the account's own site (a free tenant cannot make another) and
    add one product. Returns the product id."""
    async with SessionLocal() as db:
        site = await db.get(Site, uuid.UUID(acct.site_id))
        site.business = {"whatsapp": whatsapp} if whatsapp else {}
        await db.commit()
    res = await acct.post(f"/sites/{acct.site_id}/publish", json={})
    assert res.status_code == 200, res.text
    return await _add_product(acct, **product)


async def _add_product(acct, **product) -> str:
    body = {"name": f"P {uuid.uuid4().hex[:6]}", "price_cents": 1000, "stock": 20, **product}
    res = await acct.post(f"/sites/{acct.site_id}/products", json=body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _order(acct, product_id, *, phone="01711111111", qty=1):
    # A fresh address per call: checkout is limited to 8 orders per IP.
    return await acct.client.post(
        f"/public/site/{acct.subdomain}/orders",
        json={
            "customer": {"first_name": "Buyer", "phone": phone},
            "items": [{"product_id": product_id, "quantity": qty}],
        },
        headers={"X-Forwarded-For": f"198.51.100.{next(_ips) % 250 + 1}"},
    )


def _clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(order_limit, "_now", lambda: moment)


# ---------------------------------------------------------------------------
#  The month
# ---------------------------------------------------------------------------


def test_month_turns_over_at_dhaka_midnight(monkeypatch):
    # 17:59 UTC on 30 Nov is 23:59 in Dhaka, one minute later it is December there.
    _clock(monkeypatch, datetime(2026, 11, 30, 17, 59, tzinfo=UTC))
    assert order_limit.current_month() == date(2026, 11, 1)
    _clock(monkeypatch, datetime(2026, 11, 30, 18, 0, tzinfo=UTC))
    assert order_limit.current_month() == date(2026, 12, 1)
    _clock(monkeypatch, datetime(2026, 12, 31, 18, 0, tzinfo=UTC))
    assert order_limit.current_month() == date(2027, 1, 1)


# ---------------------------------------------------------------------------
#  Order 50 and order 51
# ---------------------------------------------------------------------------


async def test_order_50_succeeds_and_order_51_leaves_no_trace(account_factory, queued):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 49)

    assert (await _order(acct, product)).status_code == 201
    assert await _counter(acct.tenant_id) == (50, 0)
    before = (await _count(Order, acct.tenant_id), await _count(Customer, acct.tenant_id))
    stock = await _stock(product)
    notes = await _count(Notification, acct.tenant_id)
    async with SessionLocal() as db:
        number = (await db.get(OrderCounter, uuid.UUID(acct.site_id))).next_number
    queued.clear()

    # A new shopper: had the order been taken, a new customer would appear.
    res = await _order(acct, product, phone="01822222222")

    assert res.status_code == 403
    assert res.json()["detail"] == {"code": "order_limit", "whatsapp": "8801712345678"}
    assert (await _count(Order, acct.tenant_id), await _count(Customer, acct.tenant_id)) == before
    assert await _stock(product) == stock
    assert queued == [], "no email, push, SMS or Meta event for a refused order"
    assert await _count(Notification, acct.tenant_id) == notes
    async with SessionLocal() as db:
        assert (await db.get(OrderCounter, uuid.UUID(acct.site_id))).next_number == number
    assert await _counter(acct.tenant_id) == (50, 1)

    # A second try is one more attempt, still no order.
    assert (await _order(acct, product)).status_code == 403
    assert await _counter(acct.tenant_id) == (50, 2)


@pytest.mark.parametrize(
    ("business", "expected"),
    [
        ({"whatsapp": "+880 1712-345678", "phone": "01899999999"}, "8801712345678"),
        # Sign-up stores its number as `phone` only.
        ({"phone": "01899999999"}, "8801899999999"),
        ({"whatsapp": "ask us", "phone": "01899999999"}, "8801899999999"),
        ({"whatsapp": "", "phone": None}, None),
        ({}, None),
        (None, None),
    ],
)
def test_the_stores_whatsapp_number(business, expected):
    assert order_limit.store_whatsapp(business) == expected


async def test_a_store_with_no_number_gets_null(account_factory):
    acct = await account_factory("free")
    product = await _open_store(acct, whatsapp=None)
    await _set_counter(acct.tenant_id, 50)
    res = await _order(acct, product)
    assert res.status_code == 403
    assert res.json()["detail"] == {"code": "order_limit", "whatsapp": None}


async def test_a_failure_to_count_the_blocked_shopper_still_answers_403(account_factory, monkeypatch):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 50)

    def broken():
        raise OperationalError("no connection", None, Exception())

    monkeypatch.setattr(order_limit, "SessionLocal", broken)
    assert (await _order(acct, product)).status_code == 403


# ---------------------------------------------------------------------------
#  The count cannot be reset, lost or doubled
# ---------------------------------------------------------------------------


async def test_deleting_orders_and_the_site_does_not_reset_the_count(account_factory):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 49)
    assert (await _order(acct, product)).status_code == 201

    async with SessionLocal() as db:
        orders = list(
            (
                await db.execute(select(Order).where(Order.tenant_id == uuid.UUID(acct.tenant_id)))
            ).scalars()
        )
        for order in orders:
            await db.delete(order)
        await db.commit()
    assert await _count(Order, acct.tenant_id) == 0
    assert (await _order(acct, product)).status_code == 403

    res = await acct.delete(f"/sites/{acct.site_id}")
    assert res.status_code == 204, res.text
    assert (await _counter(acct.tenant_id))[0] == 50
    async with SessionLocal() as db:
        tenant = await order_limit.load_tenant(db, uuid.UUID(acct.tenant_id))
        assert not await order_limit.claim(db, tenant)
        assert not await order_limit.is_open(db, tenant)


async def test_a_failed_order_does_not_count(account_factory):
    acct = await account_factory("free")
    product = await _open_store(acct, stock=1)
    await _set_counter(acct.tenant_id, 10)
    assert (await _order(acct, product, qty=5)).status_code == 409
    assert await _counter(acct.tenant_id) == (10, 0)


async def test_an_order_that_fails_after_the_claim_does_not_count(account_factory, monkeypatch):
    """The save itself fails (a duplicate order number), after the counter was
    raised: the count, the stock and the notification all go with it."""
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 48)
    first = await _order(acct, product)
    assert first.status_code == 201
    stock = await _stock(product)

    async def taken(db, site_id):
        return first.json()["order_number"]

    with monkeypatch.context() as m:
        m.setattr(crud, "next_order_number", taken)
        assert (await _order(acct, product)).status_code == 409
    assert await _counter(acct.tenant_id) == (49, 0)
    assert await _stock(product) == stock
    assert await _limit_notes(acct.tenant_id) == []

    # The retry is counted once and is the 50th.
    assert (await _order(acct, product)).status_code == 201
    assert await _counter(acct.tenant_id) == (50, 0)
    assert len(await _limit_notes(acct.tenant_id)) == 1


async def test_dashboard_orders_do_not_count(account_factory):
    acct = await account_factory("free")
    product = await _open_store(acct)
    res = await acct.post(
        f"/sites/{acct.site_id}/orders",
        json={
            "customer": {"first_name": "Walk in", "phone": "01711111111"},
            "items": [{"product_id": product, "quantity": 1}],
        },
    )
    assert res.status_code == 201, res.text
    assert await _counter(acct.tenant_id) is None


async def test_the_month_turning_over_opens_the_store_again(account_factory, monkeypatch):
    acct = await account_factory("free")
    product = await _open_store(acct)
    nov, dec = date(2026, 11, 1), date(2026, 12, 1)
    await _set_counter(acct.tenant_id, 50, month=nov)

    _clock(monkeypatch, datetime(2026, 11, 30, 17, 59, 30, tzinfo=UTC))
    assert (await _order(acct, product)).status_code == 403
    _clock(monkeypatch, datetime(2026, 11, 30, 18, 0, 1, tzinfo=UTC))
    assert (await _order(acct, product)).status_code == 201

    assert await _counter(acct.tenant_id, nov) == (50, 1)
    assert await _counter(acct.tenant_id, dec) == (1, 0)


# ---------------------------------------------------------------------------
#  Two shoppers at once
# ---------------------------------------------------------------------------


async def test_two_concurrent_orders_at_49_exactly_one_succeeds(account_factory):
    acct = await account_factory("free")
    first = await _open_store(acct)
    # Different products and shoppers, so nothing but the counter (and the
    # order number) makes the two requests meet.
    second = await _add_product(acct)
    await _set_counter(acct.tenant_id, 49)

    results = await asyncio.gather(
        _order(acct, first, phone="01711111111"), _order(acct, second, phone="01822222222")
    )

    assert sorted(r.status_code for r in results) == [201, 403]
    assert await _counter(acct.tenant_id) == (50, 1)
    assert await _count(Order, acct.tenant_id) == 1
    assert await _count(Customer, acct.tenant_id) == 1
    assert len(await _limit_notes(acct.tenant_id)) == 1


@pytest.mark.parametrize("first_commits", [True, False])
async def test_two_open_transactions_cannot_both_take_the_last_order(account_factory, first_commits):
    """Holds the first claim open while the second arrives, so the race is
    forced rather than left to timing."""
    acct = await account_factory("free")
    await _set_counter(acct.tenant_id, 49)
    tid = uuid.UUID(acct.tenant_id)
    async with SessionLocal() as a, SessionLocal() as b:
        tenant = await order_limit.load_tenant(a, tid)
        assert await order_limit.claim(a, tenant) == 50
        waiting = asyncio.create_task(order_limit.claim(b, tenant))
        await asyncio.sleep(1.5)
        assert not waiting.done(), "the second claim must wait for the first transaction"
        await (a.commit() if first_commits else a.rollback())
        # If the first order fails, its place goes to the second.
        assert await waiting == (None if first_commits else 50)
        await b.commit()
    assert (await _counter(acct.tenant_id))[0] == 50


# ---------------------------------------------------------------------------
#  Plans without a limit
# ---------------------------------------------------------------------------


async def test_starter_counts_but_is_never_blocked_or_told(account_factory, queued):
    acct = await account_factory("starter")
    product = await _open_store(acct)
    for start in (39, 49, 500):
        await _set_counter(acct.tenant_id, start)
        assert (await _order(acct, product)).status_code == 201
        assert await _counter(acct.tenant_id) == (start + 1, 0)
    assert await _limit_notes(acct.tenant_id) == []
    assert queue.JOB_REVALIDATE_SITE not in {job for job, p in queued if p and "paths" not in p}
    async with SessionLocal() as db:
        tenant = await order_limit.load_tenant(db, uuid.UUID(acct.tenant_id))
        assert await order_limit.is_open(db, tenant)


async def test_a_broken_counter_never_blocks_a_paid_order(account_factory, monkeypatch):
    acct = await account_factory("starter")
    product = await _open_store(acct)

    async def broken(db, tenant):
        raise OperationalError("counter", None, Exception("lock timeout"))

    monkeypatch.setattr(order_limit, "claim", broken)
    res = await _order(acct, product)
    assert res.status_code == 201, res.text
    assert await _count(Order, acct.tenant_id) == 1
    assert await _counter(acct.tenant_id) is None


async def test_a_paid_checkout_does_not_wait_for_the_counter_row(account_factory):
    """Someone else holds this month's row: a paid order is saved and answered
    first, and only its counting waits."""
    acct = await account_factory("starter")
    product = await _open_store(acct)
    tid = uuid.UUID(acct.tenant_id)
    await _set_counter(acct.tenant_id, 5)
    async with SessionLocal() as holder:
        tenant = await order_limit.load_tenant(holder, tid)
        assert await order_limit.claim(holder, tenant) == 6
        request = asyncio.create_task(_order(acct, product))
        for _ in range(60):
            if await _count(Order, acct.tenant_id):
                break
            await asyncio.sleep(0.5)
        assert await _count(Order, acct.tenant_id) == 1, "the order waited for the counter"
        await holder.commit()
    assert (await request).status_code == 201
    assert await _counter(acct.tenant_id) == (7, 0)


# ---------------------------------------------------------------------------
#  Telling the merchant
# ---------------------------------------------------------------------------


async def test_notified_once_at_40_and_once_at_50(account_factory):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 38)
    for _ in range(3):  # 39, 40, 41
        assert (await _order(acct, product)).status_code == 201
    assert await _limit_notes(acct.tenant_id) == ["You have used 40 of 50 orders this month"]

    await _set_counter(acct.tenant_id, 48)
    for expected in (201, 201, 403, 403):  # 49, 50, then refused twice
        assert (await _order(acct, product)).status_code == expected
    assert await _limit_notes(acct.tenant_id) == [
        "You have used 40 of 50 orders this month",
        "You have used 50 of 50 orders this month",
    ]


async def test_a_failing_notification_does_not_fail_the_order_or_keep_the_store_open(
    account_factory, monkeypatch, queued
):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 49)

    async def broken(*a, **k):
        raise RuntimeError("notifications are down")

    monkeypatch.setattr(order_limit.notifications, "notify", broken)
    queued.clear()
    assert (await _order(acct, product)).status_code == 201
    assert await _counter(acct.tenant_id) == (50, 0)
    assert (queue.JOB_REVALIDATE_SITE, {"site_id": acct.site_id}) in queued


# ---------------------------------------------------------------------------
#  Site config
# ---------------------------------------------------------------------------


async def test_site_config_flips_at_the_50th_order(account_factory, store, queued):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 49)
    key = cache.site_key(acct.subdomain)
    url = f"/public/site/{acct.subdomain}"

    config = (await acct.client.get(url)).json()
    assert (config["ordering"], config["badge"]) == ("checkout", True)
    assert store[key][1] is None, "an open store keeps the normal cache time"

    queued.clear()
    assert (await _order(acct, product)).status_code == 201
    assert key not in store, "the cached config must be dropped at the 50th order"
    assert queued.count((queue.JOB_REVALIDATE_SITE, {"site_id": acct.site_id})) == 1

    config = (await acct.client.get(url)).json()
    assert (config["ordering"], config["badge"]) == ("whatsapp", True)

    # Refused orders change nothing a visitor sees, so nothing is dropped again.
    queued.clear()
    assert (await _order(acct, product)).status_code == 403
    assert key in store and queued == []


async def test_orders_below_the_limit_leave_the_cached_config_alone(account_factory, store, queued):
    acct = await account_factory("free")
    product = await _open_store(acct)
    await _set_counter(acct.tenant_id, 39)
    await acct.client.get(f"/public/site/{acct.subdomain}")
    queued.clear()
    assert (await _order(acct, product)).status_code == 201
    assert cache.site_key(acct.subdomain) in store
    assert queue.JOB_REVALIDATE_SITE not in {job for job, _ in queued}


async def test_a_closed_stores_cache_never_outlives_the_month(account_factory, monkeypatch, store):
    acct = await account_factory("free")
    await _open_store(acct)
    await _set_counter(acct.tenant_id, 50, month=date(2026, 12, 1))
    key = cache.site_key(acct.subdomain)
    url = f"/public/site/{acct.subdomain}"

    # 90 seconds before January in Dhaka.
    _clock(monkeypatch, datetime(2026, 12, 31, 17, 58, 30, tzinfo=UTC))
    assert (await acct.client.get(url)).json()["ordering"] == "whatsapp"
    assert store[key][1] == 90

    # Mid-month the usual cache time applies.
    store.clear()
    _clock(monkeypatch, datetime(2026, 12, 10, tzinfo=UTC))
    assert (await acct.client.get(url)).json()["ordering"] == "whatsapp"
    assert store[key][1] == order_limit.settings.cache_ttl_seconds

    store.clear()
    _clock(monkeypatch, datetime(2026, 12, 31, 18, 0, tzinfo=UTC))
    assert (await acct.client.get(url)).json()["ordering"] == "checkout"


async def test_paid_and_trial_stores_always_take_checkout_and_show_no_badge(account_factory):
    for plan in ("trial", "starter"):
        acct = await account_factory(plan)
        await _open_store(acct)
        await _set_counter(acct.tenant_id, 500)
        config = (await acct.client.get(f"/public/site/{acct.subdomain}")).json()
        assert (config["ordering"], config["badge"]) == ("checkout", False)
