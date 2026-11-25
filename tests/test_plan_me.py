"""What /auth/me tells the dashboard about the plan, the contact lifecycle for
free accounts, and the one shared paid-plan list (Task 10).

Own throwaway tenants and contact rows only; nothing here sends anything.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import delete, event as sa_event

from app import contacts, order_limit, plan_info, plans
from app.api import superadmin_billing, superadmin_contacts, superadmin_marketing as marketing
from app.api import superadmin_ops, superadmin_overview
from app.db import SessionLocal, engine
from app.models import Event, MarketingContact, Product, TenantOrderMonth


# ---------------------------------------------------------------------------
#  no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "ever_paid", "deleted", "expected"),
    [
        ("free", False, False, "free"),
        ("free", True, False, "former_customer"),  # dropped down from a paid plan
        ("free", False, True, "churned"),
        ("free", True, True, "former_customer"),
        ("starter", True, False, "customer"),
        ("trial", False, False, "trial"),
    ],
)
def test_lifecycle_for_free(plan, ever_paid, deleted, expected):
    assert contacts.lifecycle_for(plan, ever_paid, deleted) == expected


def test_every_lifecycle_list_accepts_free():
    assert marketing.AudienceIn(kind="contacts", lifecycles=["free"]).lifecycles == ["free"]
    assert "free" in superadmin_contacts.Lifecycle.__args__
    # A campaign with no groups chosen must not start mailing the new one.
    assert "free" not in marketing._DEFAULT_LIFECYCLES


def test_one_paid_plan_list():
    for module in (superadmin_billing, superadmin_overview, superadmin_ops, contacts, marketing):
        assert module.PAID_PLANS is plans.PAID_PLANS
    assert plans.PAID_PLANS == ("starter", "growth", "business")  # demo and free are not in it


# ---------------------------------------------------------------------------
#  /auth/me
# ---------------------------------------------------------------------------


async def test_free_plan_info_is_exact(account_factory):
    acct = await account_factory("free")
    tid, sid = uuid.UUID(acct.tenant_id), uuid.UUID(acct.site_id)
    month = order_limit.current_month()
    last_month = date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)
    async with SessionLocal() as db:
        for i in range(2):
            db.add(Product(tenant_id=tid, site_id=sid, name=f"P{i}", slug=f"p-{i}", currency="BDT"))
        db.add(Event(tenant_id=tid, site_id=sid, name="E", slug="e", discount_percent=10))
        db.add(TenantOrderMonth(tenant_id=tid, month=month, orders=7, blocked=3))
        # An old month must not leak into this month's numbers.
        db.add(TenantOrderMonth(tenant_id=tid, month=last_month, orders=50, blocked=9))
        await db.commit()

    statements: list[str] = []

    def _spy(conn, cursor, statement, *a):
        statements.append(statement)

    sa_event.listen(engine.sync_engine, "before_cursor_execute", _spy)
    try:
        res = await acct.get("/auth/me")
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", _spy)

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"user", "tenant", "plan_info"}
    assert body["tenant"]["plan"] == "free"
    assert body["plan_info"] == {
        "key": "free",
        "name": "Free",
        "features": [],
        "limits": {
            "products": 10, "storage_bytes": 100 * 1024 * 1024, "orders_per_month": 50,
            "stores": 1, "ai_per_day": 0, "events": 3,
        },
        "usage": {
            "products": 2, "events": 1, "stores": 1,
            "orders_this_month": 7, "blocked_this_month": 3,
        },
        # Written out: the dashboard shows exactly these totals, in cents.
        "prices": {
            "starter": {"1": 49_900, "3": 142_500, "6": 269_400, "12": 478_800},
            "growth": {"1": 149_900, "3": 427_500, "6": 809_400, "12": 1_438_800},
            "business": {"1": 399_900, "3": 1_139_700, "6": 2_159_400, "12": 3_838_800},
        },
    }
    # The login lookup plus ONE usage query, not one per counter.
    assert len(statements) <= 3, statements


async def test_paid_plan_info_has_sorted_features_and_unlimited_as_null(account_factory):
    acct = await account_factory("business")
    info = (await acct.get("/auth/me")).json()["plan_info"]
    assert info["key"] == "business" and info["name"] == "Business"
    assert info["features"] == sorted(plans.PLANS["business"].features)
    assert info["limits"]["products"] is None and info["limits"]["orders_per_month"] is None
    assert info["limits"]["stores"] == 2
    # No counter row yet this month: zeros, not nulls.
    assert info["usage"] == {
        "products": 0, "events": 0, "stores": 1, "orders_this_month": 0, "blocked_this_month": 0,
    }


async def test_a_usage_failure_never_fails_me(account_factory, monkeypatch):
    acct = await account_factory("starter")

    async def boom(db, tenant_id):
        raise RuntimeError("usage is down")

    monkeypatch.setattr(plan_info, "_usage", boom)
    res = await acct.get("/auth/me")

    assert res.status_code == 200
    body = res.json()
    assert body["plan_info"]["usage"] is None
    assert body["plan_info"]["key"] == "starter"
    assert body["tenant"]["plan"] == "starter" and body["user"]["email"] == acct.email


# ---------------------------------------------------------------------------
#  campaigns must not start mailing the new groups
# ---------------------------------------------------------------------------


async def test_default_audiences_skip_free_owners_and_contacts(account_factory, monkeypatch):
    free = await account_factory("free")
    paid = await account_factory("starter")
    monkeypatch.setattr(marketing.contact_checks, "is_mailable", lambda e: True)
    email = f"free-contact-{uuid.uuid4().hex[:10]}@example.com"
    async with SessionLocal() as db:
        db.add(MarketingContact(email=email, lifecycle="free", plan="free", source="signup"))
        await db.commit()
    try:
        async with SessionLocal() as db:
            owners = await marketing._resolve(db, marketing.AudienceIn(kind="owners"))
            by_default = await marketing._resolve(db, marketing.AudienceIn(kind="contacts"))
            chosen = await marketing._resolve(
                db, marketing.AudienceIn(kind="contacts", lifecycles=["free"])
            )
        assert paid.email.lower() in owners and free.email.lower() not in owners
        assert email not in by_default
        assert email in chosen
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(MarketingContact).where(MarketingContact.email == email))
            await db.commit()


async def test_profile_edit_returns_plan_info_too(account_factory):
    acct = await account_factory("free")
    body = (await acct.patch("/auth/me", json={"full_name": "New Name"})).json()
    assert body["plan_info"]["key"] == "free" and body["plan_info"]["usage"]["stores"] == 1
