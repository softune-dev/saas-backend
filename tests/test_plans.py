"""The plan table (app/plans.py) is the single source of limits, features and
prices. These tests pin every cell of it, and prove each older per-module
helper now reads from it instead of keeping a copy that can drift.

Nobody is on plan "free" yet (the tenants CHECK constraint does not allow it
until a later migration), so free is tested through has_feature /
ensure_feature directly; the DB-backed gate tests use starter/trial/growth.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app import ai, ai_tools, events, invoices, media, plan_gate, plans, products
from app.db import SessionLocal
from app.models import Tenant

MB = 1024 * 1024
GB = 1024 * MB

STARTER_FEATURES = {"gateways", 
    "customers", "pos", "seo", "fraud", "domain", "ai", "chatgpt",
    "theme_switch", "ecourier", "full_analytics", "no_badge",
}
GROWTH_FEATURES = STARTER_FEATURES | {"all_couriers", "dropship"}

# key: (products, storage, orders, stores, ai/day, events, price)
LIMITS = {
    "free": (10, 100 * MB, 50, 1, 0, None, 0),
    "starter": (50, 500 * MB, None, 1, 30, None, 49_900),
    "growth": (500, 2 * GB, None, 1, 100, None, 149_900),
    "business": (None, 3 * GB, None, 2, 150, None, 399_900),
    "trial": (50, 500 * MB, None, 1, 15, None, 0),
    "demo": (50, 800 * MB, None, 1, 50, None, 0),
}
FEATURE_SETS = {
    "free": {"gateways"},
    "starter": STARTER_FEATURES,
    "growth": GROWTH_FEATURES,
    "business": GROWTH_FEATURES,
    "trial": STARTER_FEATURES - {"theme_switch"},
    "demo": set(plans.FEATURES),
}


# ---------------------------------------------------------------------------
#  The table itself
# ---------------------------------------------------------------------------


def test_table_has_exactly_the_documented_plans():
    assert set(plans.PLANS) == set(LIMITS)
    assert plans.PAID_PLANS == ("starter", "growth", "business")
    assert len(plans.FEATURES) == 14


@pytest.mark.parametrize("key", LIMITS)
def test_every_limit_cell(key):
    p = plans.PLANS[key]
    n_products, storage, orders, stores, ai_day, n_events, price = LIMITS[key]
    assert p.key == key
    assert (p.products, p.storage_bytes, p.orders_per_month) == (n_products, storage, orders)
    assert (p.stores, p.ai_per_day, p.events, p.price_cents) == (stores, ai_day, n_events, price)


@pytest.mark.parametrize("key", LIMITS)
def test_every_feature_cell(key):
    assert plans.PLANS[key].features == frozenset(FEATURE_SETS[key])


def test_unknown_or_missing_plan_falls_to_free():
    assert plans.get_plan("nonsense").key == "free"
    assert plans.get_plan(None).key == "free"


def test_needed_plan_is_the_cheapest_paid_plan_with_the_feature():
    assert plans.needed_plan("domain") == "starter"
    assert plans.needed_plan("gateways") == "starter"  # every paid plan, and Free
    assert plans.needed_plan("all_couriers") == "growth"
    assert plans.needed_plan("dropship") == "growth"


def test_unknown_feature_is_a_programming_error():
    with pytest.raises(ValueError):
        plans.has_feature("free", "seoo")
    with pytest.raises(ValueError):
        plans.needed_plan("seoo")
    with pytest.raises(ValueError):
        plans.ensure_feature("starter", "seoo")


def test_ensure_feature_passes_when_the_plan_has_it():
    plans.ensure_feature("starter", "seo")
    plans.ensure_feature("demo", "gateways")


def test_ensure_feature_raises_structured_403():
    with pytest.raises(HTTPException) as exc:
        plans.ensure_feature("free", "seo")
    assert exc.value.status_code == 403
    detail = exc.value.detail
    assert set(detail) == {"code", "feature", "needed_plan", "message"}
    assert detail["code"] == "plan_locked"
    assert detail["feature"] == "seo"
    assert detail["needed_plan"] == "starter"
    assert isinstance(detail["message"], str) and detail["message"]


# ---------------------------------------------------------------------------
#  Older helpers now read from the table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", LIMITS)
def test_helpers_agree_with_the_table(key):
    p = plans.PLANS[key]
    assert ai.plan_cap(key) == p.ai_per_day
    assert products.plan_product_limit(key) == p.products
    assert media.plan_storage_limit(key) == p.storage_bytes
    assert events.plan_event_limit(key) == p.events
    assert invoices.PLAN_PRICES_CENTS[key] == p.price_cents


@pytest.mark.parametrize("key", LIMITS)
async def test_billing_status_ai_cap_comes_from_the_table(key, monkeypatch):
    """ai_tools used to keep its own AI dict (with no trial entry, so a trial
    was told 80 requests a day instead of 15). Same value as ai.plan_cap now."""

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

        def scalars(self):
            return self

        def first(self):
            return None  # no site, so no Cloudinary lookup

    tenant = SimpleNamespace(
        plan=key, status="active", trial_expires_at=None, plan_renews_at=None
    )

    class _Db:
        async def execute(self, _stmt):
            return _Result(tenant)

    class _Cache:
        async def get(self, _key):
            return None

    monkeypatch.setattr(ai_tools.cache, "client", lambda: _Cache())
    status_ = await ai_tools._get_billing_status(_Db(), uuid.uuid4())
    assert status_["ai_requests_daily_limit"] == plans.PLANS[key].ai_per_day
    assert status_["storage_limit_mb"] == round(plans.PLANS[key].storage_bytes / MB, 1)


def test_unlimited_products_never_raises():
    products.ensure_within_product_limit(100_000, "business")


def test_limited_products_still_raise_a_plain_string():
    with pytest.raises(HTTPException) as exc:
        products.ensure_within_product_limit(50, "starter")
    assert exc.value.status_code == 403
    assert isinstance(exc.value.detail, str)
    assert "50" in exc.value.detail


def test_starter_may_switch_themes_trial_may_not():
    plans.ensure_theme_switch_allowed("starter")
    plans.ensure_theme_switch_allowed("growth")
    plans.ensure_theme_switch_allowed("demo")
    with pytest.raises(HTTPException) as exc:
        plans.ensure_theme_switch_allowed("trial")
    assert isinstance(exc.value.detail, str)
    with pytest.raises(HTTPException):
        plans.ensure_theme_switch_allowed("free")


@pytest.mark.parametrize("key", ["free", "starter", "growth", "business", "trial", "demo"])
def test_every_plan_has_every_payment_method(key):
    for provider in sorted(plans.GATED_PAYMENT_PROVIDERS) + ["cod", "manual"]:
        plans.ensure_payment_provider_allowed(key, provider)


@pytest.mark.parametrize("key", ["starter", "trial"])
def test_couriers_below_growth(key):
    plans.ensure_courier_allowed(key, "steadfast")  # always allowed
    for provider in ("pathao", "redx", "paperfly"):
        with pytest.raises(HTTPException) as exc:
            plans.ensure_courier_allowed(key, provider)
        assert isinstance(exc.value.detail, str)
        assert "Upgrade to connect more couriers" in exc.value.detail
    plans.ensure_courier_allowed(key, "ecourier")


@pytest.mark.parametrize("key", ["growth", "business", "demo"])
def test_all_couriers_on_growth_and_up(key):
    for provider in ("steadfast", "ecourier", "pathao", "redx", "paperfly"):
        plans.ensure_courier_allowed(key, provider)


# ---------------------------------------------------------------------------
#  app/plan_gate.py (live plan, FastAPI dependency)
# ---------------------------------------------------------------------------


def test_require_feature_rejects_an_unknown_feature_at_decoration_time():
    with pytest.raises(ValueError):
        plan_gate.require_feature("seoo")


def _gated_app() -> FastAPI:
    gate_app = FastAPI()

    @gate_app.get("/gateways", dependencies=[Depends(plan_gate.require_feature("all_couriers"))])
    async def gateways() -> dict:
        return {"ok": True}

    @gate_app.get("/seo", dependencies=[Depends(plan_gate.require_feature("seo"))])
    async def seo() -> dict:
        return {"ok": True}

    return gate_app


async def _set_plan(tenant_id: str, plan: str) -> None:
    # Own tenant only: the row was created by the account_factory fixture.
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(tenant_id)).values(plan=plan))
        await db.commit()


async def test_tenant_plan_reads_live_and_missing_tenant_is_404(account_factory):
    acct = await account_factory("starter")
    async with SessionLocal() as db:
        assert await plan_gate.tenant_plan(db, uuid.UUID(acct.tenant_id)) == "starter"
    await _set_plan(acct.tenant_id, "growth")
    async with SessionLocal() as db:
        assert await plan_gate.tenant_plan(db, uuid.UUID(acct.tenant_id)) == "growth"
        with pytest.raises(HTTPException) as exc:
            await plan_gate.tenant_plan(db, uuid.uuid4())
    assert exc.value.status_code == 404


async def test_ensure_uses_the_live_plan(account_factory):
    acct = await account_factory("starter")
    tid = uuid.UUID(acct.tenant_id)
    async with SessionLocal() as db:
        await plan_gate.ensure(db, tid, "seo")  # starter has it
        with pytest.raises(HTTPException) as exc:
            await plan_gate.ensure(db, tid, "all_couriers")
    assert exc.value.detail["code"] == "plan_locked"
    assert exc.value.detail["needed_plan"] == "growth"


@pytest.mark.parametrize("plan", ["starter", "trial"])
async def test_require_feature_blocks_then_follows_a_plan_change(account_factory, plan):
    acct = await account_factory(plan)
    gate_app = _gated_app()
    async with AsyncClient(transport=ASGITransport(app=gate_app), base_url="http://t") as c:
        blocked = await c.get("/gateways", headers=acct.headers)
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["code"] == "plan_locked"
        assert blocked.json()["detail"]["needed_plan"] == "growth"

        # Same token, plan changed in the database: the gate must notice.
        await _set_plan(acct.tenant_id, "growth")
        assert (await c.get("/gateways", headers=acct.headers)).status_code == 200


async def test_require_feature_lets_a_plan_that_has_it_through(account_factory):
    acct = await account_factory("starter")
    async with AsyncClient(transport=ASGITransport(app=_gated_app()), base_url="http://t") as c:
        assert (await c.get("/seo", headers=acct.headers)).status_code == 200



@pytest.mark.parametrize(
    ("check", "provider", "feature", "needed"),
    [
        (plans.ensure_courier_allowed, "ecourier", "ecourier", "starter"),
        (plans.ensure_courier_allowed, "pathao", "all_couriers", "growth"),
    ],
)
def test_free_gets_the_structured_lock_for_couriers(check, provider, feature, needed):
    with pytest.raises(HTTPException) as exc:
        check("free", provider)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "plan_locked"
    assert (exc.value.detail["feature"], exc.value.detail["needed_plan"]) == (feature, needed)
    plans.ensure_payment_provider_allowed("free", "cod")
    plans.ensure_courier_allowed("free", "steadfast")
