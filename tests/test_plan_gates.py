"""Plan locks on the dashboard routes: customers, POS, fraud, Meta, analytics.

The route table is walked inside one test per plan, not one test per route:
every account here is created in the real database, and forty tenants to check
forty routes would hold the shared test lock for most of an hour. All failures
are collected, so a red run still names every route that is wrong.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app import basic_plan, cache, crud, edge_config, queue, recaptcha
from app.api import customers, fraud, manage, marketing
from app.db import SessionLocal
from app.models import Customer, Order, PageView, Site, Tenant

# A row id that exists nowhere: the lock must answer before the row lookup on
# free, and on starter the normal 404 proves the call got past the lock.
NOWHERE = "00000000-0000-4000-8000-000000000000"

# (feature, method, path under /sites/{id}, body, status on starter)
LOCKED_ROUTES = [
    ("customers", "GET", "/customers", None, 200),
    ("customers", "GET", f"/customers/{NOWHERE}", None, 404),
    ("customers", "PATCH", f"/customers/{NOWHERE}", {"name": "x"}, 404),
    ("customers", "DELETE", f"/customers/{NOWHERE}", None, 404),
    ("customers", "POST", "/customers/bulk-delete", {"ids": [NOWHERE]}, 200),
    ("customers", "GET", "/abandoned-checkouts", None, 200),
    ("customers", "DELETE", f"/abandoned-checkouts/{NOWHERE}", None, 404),
    ("customers", "DELETE", "/abandoned-checkouts", None, 200),
    ("fraud", "GET", "/fraud/blocklist", None, 200),
    ("fraud", "POST", "/fraud/blocklist", {"phone": "01712345678", "note": ""}, 201),
    ("fraud", "DELETE", f"/fraud/blocklist/{NOWHERE}", None, 404),
    ("fraud", "GET", "/fraud/ip-blocklist", None, 200),
    ("fraud", "POST", "/fraud/ip-blocklist", {"ip_address": "198.51.100.7", "note": ""}, 201),
    ("fraud", "DELETE", f"/fraud/ip-blocklist/{NOWHERE}", None, 404),
    ("fraud", "GET", "/fraud/suspicious-orders", None, 200),
    ("fraud", "POST", f"/fraud/suspicious-orders/{NOWHERE}/review", {"decision": "cleared"}, 404),
    ("seo", "GET", "/marketing", None, 200),
    ("seo", "POST", "/marketing/meta-capi", {"access_token": "EAAB" + "a1B2c3" * 12}, 201),
    ("seo", "PATCH", f"/marketing/{NOWHERE}", {"test_event_code": "TEST1"}, 404),
]


def _locked(feature: str) -> dict:
    """The whole refusal, exactly: the dashboard reads these four keys."""
    return {
        "code": "plan_locked",
        "feature": feature,
        "needed_plan": "starter",
        "message": "This is available on the Starter plan and above.",
    }


@pytest.fixture(autouse=True)
def _no_outside_services(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _nothing(*args, **kwargs):
        return None

    async def _human(*args, **kwargs):
        return recaptcha.VerifyResult(ok=True)

    monkeypatch.setattr(queue, "publish", _nothing)
    monkeypatch.setattr(edge_config, "sync_ip_blocks", _nothing)
    monkeypatch.setattr(recaptcha, "verify", _human)


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A working cache: there is no Redis here, and the cache tests are about
    what a warm cache would hand back."""
    store: dict = {}

    async def _get(key):
        return store.get(key)

    async def _set(key, value, ttl=None):
        store[key] = value

    monkeypatch.setattr(cache, "get_json", _get)
    monkeypatch.setattr(cache, "set_json", _set)
    return store


async def _call(acct, method: str, path: str, body):
    return await acct.client.request(
        method, f"/sites/{acct.site_id}{path}", headers=acct.headers, json=body
    )


async def _set_plan(acct, plan: str) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == acct.tenant_id).values(plan=plan))
        await db.commit()


async def _seed_order(acct, number: str, *, created_at: datetime | None = None, **kw) -> str:
    """An order row of this test's own tenant, with everything filled in that
    a free plan must not see."""
    values = {
        "customer": {"first_name": "Rahim", "phone": "01712345678", "address": "House 1"},
        "total_cents": 1000,
        "subtotal_cents": 1000,
        "currency": "BDT",
        "ip_address": "198.51.100.9",
        "device_id": "device-1",
        "fraud_status": "flagged",
        "fraud_reason": "burst",
        **kw,
    }
    async with SessionLocal() as db:
        order = Order(
            tenant_id=acct.tenant_id, site_id=acct.site_id, order_number=number, **values
        )
        if created_at is not None:
            order.created_at = created_at
        db.add(order)
        await db.commit()
        return str(order.id)


async def _seed_customer(acct, phone: str = "1712345678") -> str:
    async with SessionLocal() as db:
        row = Customer(tenant_id=acct.tenant_id, site_id=acct.site_id, phone=phone, name="Rahim")
        db.add(row)
        await db.commit()
        return str(row.id)


async def _product(acct, **kw) -> dict:
    body = {"name": "Widget", "price_cents": 1000, "stock": 50, "sku": "SKU-777", **kw}
    made = await acct.post(f"/sites/{acct.site_id}/products", json=body)
    assert made.status_code == 201, made.text
    return made.json()


# ---------------------------------------------------------------------------
#  Locks
# ---------------------------------------------------------------------------


def test_every_route_of_the_locked_routers_has_a_gate():
    """A route added to these files later must not be forgotten: the gate is
    looked up on the route itself, no database involved."""

    def gated(route) -> bool:
        return any(
            getattr(d.call, "__qualname__", "").startswith("require_feature.")
            for d in route.dependant.dependencies
        )

    for router in (customers.router, fraud.router):
        assert [r.path for r in router.routes if not gated(r)] == []
    # Disconnecting Meta stays open: a merchant who lost the plan must still
    # be able to remove their own token.
    assert [(r.path, sorted(r.methods)) for r in marketing.router.routes if not gated(r)] == [
        ("/sites/{site_id}/marketing/{connection_id}", ["DELETE"])
    ]
    locked = [r.path for r in manage.router.routes if gated(r)]
    assert sorted(locked) == [
        "/sites/{site_id}/abandoned-checkouts",
        "/sites/{site_id}/abandoned-checkouts/{checkout_id}",
        "/sites/{site_id}/customers/bulk-delete",
        "/sites/{site_id}/customers/{customer_id}",
    ]


async def test_free_is_refused_on_every_locked_route(account_factory):
    acct = await account_factory("free")
    product = await _product(acct)
    wrong = []
    for feature, method, path, body, _ in LOCKED_ROUTES:
        resp = await _call(acct, method, path, body)
        if resp.status_code != 403 or resp.json() != {"detail": _locked(feature)}:
            wrong.append((method, path, resp.status_code, resp.text))

    line = [{"product_id": product["id"], "quantity": 1}]
    for extra in ({"channel": "pos"}, {"channel": "storefront"}, {}):
        resp = await _call(acct, "POST", "/orders", {"items": line, **extra})
        if resp.status_code != 403 or resp.json() != {"detail": _locked("pos")}:
            wrong.append(("POST", "/orders", extra, resp.status_code, resp.text))
    assert wrong == []

    async with SessionLocal() as db:
        site_id = uuid.UUID(acct.site_id)
        assert await db.scalar(select(func.count(Order.id)).where(Order.site_id == site_id)) == 0
    after = await acct.get(f"/sites/{acct.site_id}/products/{product['id']}")
    assert after.json()["stock"] == 50, "a refused sale must not touch stock"

    # Open on free: scanning a SKU, and disconnecting Meta (404: no such row).
    scan = await acct.get(f"/sites/{acct.site_id}/products?q=SKU-777")
    assert scan.status_code == 200 and scan.json()["total"] == 1
    assert (await _call(acct, "DELETE", f"/marketing/{NOWHERE}", None)).status_code == 404

    # The lock is about the caller's own plan, so a site that isn't theirs
    # answers exactly the same and confirms nothing.
    other = await acct.get(f"/sites/{NOWHERE}/customers")
    assert other.status_code == 403 and other.json() == {"detail": _locked("customers")}


async def test_starter_is_not_locked_anywhere(account_factory):
    acct = await account_factory("starter")
    product = await _product(acct)
    wrong = []
    for _, method, path, body, expected in LOCKED_ROUTES:
        resp = await _call(acct, method, path, body)
        if resp.status_code != expected:
            wrong.append((method, path, resp.status_code, resp.text))
    line = [{"product_id": product["id"], "quantity": 1}]
    for extra in ({"channel": "pos"}, {"channel": "storefront"}, {}):
        resp = await _call(acct, "POST", "/orders", {"items": line, **extra})
        if resp.status_code != 201:
            wrong.append(("POST", "/orders", extra, resp.status_code, resp.text))
    if (await acct.get(f"/sites/{NOWHERE}/customers")).status_code != 404:
        wrong.append("someone else's site must still be 404")
    assert wrong == []


async def test_cached_customers_are_not_served_after_a_downgrade(account_factory, fake_cache):
    """The customers list is cached per site: the paid period's copy must not
    be what a free request gets."""
    acct = await account_factory("starter")
    await _seed_customer(acct)
    warm = await acct.get(f"/sites/{acct.site_id}/customers")
    assert warm.json()["total"] == 1
    assert fake_cache, "the paid read should have filled the cache"

    await _set_plan(acct, "free")
    resp = await acct.get(f"/sites/{acct.site_id}/customers")
    assert resp.status_code == 403 and resp.json() == {"detail": _locked("customers")}


# ---------------------------------------------------------------------------
#  Checkout is not behind any of these locks
# ---------------------------------------------------------------------------


async def test_checkout_on_free_saves_the_customer_and_still_blocks_a_listed_phone(account_factory):
    """The blocklist is enforced from the table, not through the gated routes,
    so entries made while paid keep protecting a free store."""
    acct = await account_factory("starter")
    product = await _product(acct)
    added = await acct.post(
        f"/sites/{acct.site_id}/fraud/blocklist", json={"phone": "01899999999", "note": ""}
    )
    assert added.status_code == 201
    await _set_plan(acct, "free")
    async with SessionLocal() as db:
        await db.execute(update(Site).where(Site.id == acct.site_id).values(status="published"))
        await db.commit()

    async def buy(phone: str, ip: str):
        return await acct.client.post(
            f"/public/site/{acct.subdomain}/orders",
            json={
                "customer": {"first_name": "Rahim", "phone": phone},
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
            headers={"X-Forwarded-For": ip},
        )

    ok = await buy("01712345678", "198.51.100.21")
    assert ok.status_code == 201, ok.text
    blocked = await buy("01899999999", "198.51.100.22")
    assert blocked.status_code == 403
    assert "plan_locked" not in blocked.text

    async with SessionLocal() as db:
        rows = (
            await db.execute(select(Customer).where(Customer.tenant_id == uuid.UUID(acct.tenant_id)))
        ).scalars().all()
        assert [r.phone for r in rows] == [crud.normalize_phone("01712345678")]


# ---------------------------------------------------------------------------
#  What an order shows on free
# ---------------------------------------------------------------------------

_HIDDEN = ("ip_address", "device_id", "fraud_status", "fraud_reason", "customer_id")


async def test_orders_on_free_hide_fraud_and_customer_fields(account_factory):
    acct = await account_factory("free")
    customer_id = await _seed_customer(acct)
    oid = await _seed_order(acct, "ORD-1", customer_id=uuid.UUID(customer_id))
    base = f"/sites/{acct.site_id}/orders"

    listed = (await acct.get(base)).json()["items"][0]
    one = (await acct.get(f"{base}/{oid}")).json()
    patched = (await acct.patch(f"{base}/{oid}", json={"status": "paid"})).json()
    for out in (listed, one, patched):
        assert {k: out[k] for k in _HIDDEN} == dict.fromkeys(_HIDDEN)
        assert out["customer"] == {
            "first_name": "Rahim", "phone": "01712345678", "address": "House 1"
        }
    assert patched["status"] == "paid"

    # Hidden from the answer, not wiped from the row: upgrading brings it back.
    await _set_plan(acct, "starter")
    back = (await acct.get(f"{base}/{oid}")).json()
    assert back["customer_id"] == customer_id
    assert back["fraud_status"] == "flagged" and back["fraud_reason"] == "burst"
    assert back["ip_address"] == "198.51.100.9" and back["device_id"] == "device-1"


async def test_courier_booking_answer_is_hidden_on_free_too(account_factory, monkeypatch):
    from app import courier_booking

    acct = await account_factory("free")
    oid = await _seed_order(acct, "ORD-1")

    async def _connection(*args, **kwargs):
        return object()

    async def _booked(db, order, connection):
        return True, None

    monkeypatch.setattr(courier_booking, "get_steadfast_connection", _connection)
    monkeypatch.setattr(courier_booking, "book_order", _booked)
    resp = await acct.post(f"/sites/{acct.site_id}/orders/{oid}/courier/book")
    assert resp.status_code == 200, resp.text
    assert {k: resp.json()[k] for k in _HIDDEN} == dict.fromkeys(_HIDDEN)


def test_order_window_starts_at_dhaka_midnight_of_the_previous_month():
    def start(*at) -> datetime:
        return basic_plan.order_window_start(datetime(*at, tzinfo=UTC))

    # November in Dhaka: October 1st 00:00 there is 18:00 UTC the day before.
    assert start(2026, 11, 23, 12) == datetime(2026, 9, 30, 18, tzinfo=UTC)
    # 17:59 UTC on the 30th is still November in Dhaka; 18:00 is December.
    assert start(2026, 11, 30, 17, 59) == datetime(2026, 9, 30, 18, tzinfo=UTC)
    assert start(2026, 11, 30, 18, 0) == datetime(2026, 10, 31, 18, tzinfo=UTC)
    # January looks back into the year before.
    assert start(2027, 1, 10, 0) == datetime(2026, 11, 30, 18, tzinfo=UTC)


async def test_orders_list_on_free_is_the_current_and_previous_month(account_factory, fake_cache):
    acct = await account_factory("starter")
    start = basic_plan.order_window_start()
    await _seed_order(acct, "NEW")
    await _seed_order(acct, "EDGE", created_at=start)
    old_id = await _seed_order(acct, "OLD", created_at=start - timedelta(seconds=1))
    base = f"/sites/{acct.site_id}/orders"

    async def numbers(query: str = "") -> tuple[list[str], int]:
        body = (await acct.get(f"{base}{query}")).json()
        return [o["order_number"] for o in body["items"]], body["total"]

    # Paid first, so every one of these answers is sitting in the cache.
    queries = ("", "?status=pending", "?q=01712345678", "?limit=500", "?offset=2")
    assert await numbers() == (["NEW", "EDGE", "OLD"], 3)
    for query in queries:
        assert (await numbers(query))[1] == 3

    await _set_plan(acct, "free")
    for query in queries:
        found, total = await numbers(query)
        assert "OLD" not in found and total == 2, query
    # Looking for the old order by its number is not a way round the window.
    assert await numbers("?q=OLD") == ([], 0)
    # A link to one order still opens it.
    assert (await acct.get(f"{base}/{old_id}")).status_code == 200

    # And the other way: the short list is not what an upgrade gets back.
    await _set_plan(acct, "starter")
    assert await numbers() == (["NEW", "EDGE", "OLD"], 3)
    assert (await acct.get(base)).json()["items"][0]["ip_address"] == "198.51.100.9"


# ---------------------------------------------------------------------------
#  Analytics
# ---------------------------------------------------------------------------


async def test_analytics_on_free_is_seven_days_of_three_numbers(account_factory, fake_cache):
    acct = await account_factory("starter")
    now = datetime.now(UTC)
    for number, cents, days, status in (
        ("A1", 1500, 1, "pending"),
        ("A2", 700, 6, "fulfilled"),
        ("LOST", 300, 2, "cancelled"),
        ("OLD", 9000, 8, "fulfilled"),
    ):
        await _seed_order(
            acct, number, total_cents=cents, status=status, created_at=now - timedelta(days=days)
        )
    async with SessionLocal() as db:
        for session_id, days in (("s1", 1), ("s1", 2), ("s2", 3), ("s3", 8)):
            db.add(
                PageView(
                    tenant_id=acct.tenant_id, site_id=acct.site_id, path="/",
                    session_id=session_id, created_at=now - timedelta(days=days),
                )
            )
        await db.commit()
    url = f"/sites/{acct.site_id}/analytics?weeks=26"

    paid = (await acct.get(url)).json()
    assert paid["revenue"]["cents"] == 11200 and paid["visits"]["count"] == 3
    assert len(paid["revenue_curve"]) == 26 and len(paid["sales_report"]) == 26
    assert fake_cache, "the paid read should have filled the cache"

    await _set_plan(acct, "free")
    free = (await acct.get(url)).json()
    empty = {"cents": None, "count": None, "percent": None, "change_percent": None}
    assert free == {
        "currency": "BDT",
        "revenue": {**empty, "cents": 2200},
        "orders": {**empty, "count": 3},
        "visits": {**empty, "count": 2},
        "aov": empty, "refund_rate": empty, "conversion_rate": empty, "profit": empty,
        "cost_data_coverage_percent": 0.0,
        "revenue_curve": [], "category_shares": [], "best_sellers": [], "sales_report": [],
    }
    # One week asked for is the same seven days, not a different report.
    assert (await acct.get(f"/sites/{acct.site_id}/analytics?weeks=1")).json() == free

    await _set_plan(acct, "starter")
    assert (await acct.get(url)).json() == paid


async def test_analytics_on_free_keeps_the_stores_currency_in_a_quiet_week(account_factory):
    acct = await account_factory("free")
    await _seed_order(acct, "OLD", created_at=datetime.now(UTC) - timedelta(days=40))
    body = (await acct.get(f"/sites/{acct.site_id}/analytics")).json()
    assert body["currency"] == "BDT"
    assert body["revenue"]["cents"] == 0 and body["orders"]["count"] == 0
    assert (await acct.get(f"/sites/{NOWHERE}/analytics")).status_code == 404


# ---------------------------------------------------------------------------
#  Locked preview: the number behind the blur
# ---------------------------------------------------------------------------


async def test_locked_preview_is_a_count_and_nothing_else(account_factory):
    acct = await account_factory("free")
    base = f"/sites/{acct.site_id}/locked-preview"
    assert (await acct.get(f"{base}/customers")).json() == {"count": 0}

    await _seed_customer(acct)
    await _seed_order(acct, "FLAGGED")
    await _seed_order(acct, "FINE", fraud_status="clear")
    for feature in ("customers", "fraud"):
        resp = await acct.get(f"{base}/{feature}")
        assert resp.status_code == 200 and resp.json() == {"count": 1}, feature
    # Not a preview: a real plan feature without one, and a made-up name.
    assert (await acct.get(f"{base}/seo")).status_code == 404
    assert (await acct.get(f"{base}/nonsense")).status_code == 404
