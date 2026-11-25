"""The admin's Free / Paid / Internal views and the deleted-accounts record
(app/api/superadmin_plans.py). Own throwaway tenants only; the handlers are
called directly with a superadmin Principal, as in test_superadmin_billing."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app import plans
from app.api import superadmin_plans as sp
from app.db import SessionLocal
from app.models import DeletedAccount, Product, Tenant, TenantOrderMonth
from app import order_limit
from app.security import Principal


def _admin(account) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(account.tenant_id), role="owner", is_superadmin=True
    )


async def test_routes_are_hidden_from_merchants(account):
    for path in ("/superadmin/plan-tenants", "/superadmin/plan-overview", "/superadmin/deleted-accounts"):
        assert (await account.get(path)).status_code == 404


async def test_free_row_shows_usage_and_deletion_date(account_factory):
    acct = await account_factory("free")
    tid = uuid.UUID(acct.tenant_id)
    active = datetime.now(UTC) - timedelta(days=10)
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == tid).values(last_active_at=active))
        for i in range(8):
            db.add(Product(tenant_id=tid, site_id=uuid.UUID(acct.site_id), name=f"P{i}", slug=f"p-{i}", currency="BDT"))
        db.add(TenantOrderMonth(tenant_id=tid, month=order_limit.current_month(), orders=41, blocked=2))
        await db.commit()

    async with SessionLocal() as db:
        out = await sp.plan_tenants(_admin(acct), db, group="free", q=acct.subdomain, limit=10, offset=0)
    assert out["total"] == 1
    row = out["items"][0]
    assert (row["products"], row["product_limit"]) == (8, 10)
    assert (row["orders_this_month"], row["order_limit"], row["blocked_this_month"]) == (41, 50, 2)
    assert row["deletes_at"] == active + timedelta(days=plans.FREE_INACTIVE_DAYS)
    assert set(out["counts"]) == {"free", "paid", "internal"}

    async with SessionLocal() as db:
        overview = await sp.plan_overview(_admin(acct), db)
    assert acct.tenant_id in {str(b["id"]) for b in overview["near_limit"]}


async def test_groups_never_mix(account_factory):
    paid = await account_factory("growth")
    async with SessionLocal() as db:
        free = await sp.plan_tenants(_admin(paid), db, group="free", q=paid.subdomain, limit=10, offset=0)
        mine = await sp.plan_tenants(_admin(paid), db, group="paid", q=paid.subdomain, limit=10, offset=0)
    assert free["total"] == 0
    assert mine["total"] == 1 and mine["items"][0]["deletes_at"] is None


async def test_deleted_accounts_are_listed_and_searchable(account):
    email = f"gone-{uuid.uuid4().hex[:8]}@example.com"
    async with SessionLocal() as db:
        row = DeletedAccount(
            tenant_id=uuid.UUID(account.tenant_id), reason="inactive", email=email, phone="01711111111",
            shop_name="Gone Shop", subdomain="gone-shop", plan="free", ever_paid=False,
            snapshot={"products": [{"name": "Kept"}]},
        )
        db.add(row)
        await db.commit()
        record_id = row.id

    async with SessionLocal() as db:
        found = await sp.deleted_accounts(_admin(account), db, q=email, limit=10, offset=0)
        detail = await sp.deleted_account(record_id, _admin(account), db)
    assert [r.id for r in found["items"]] == [record_id]
    assert detail.snapshot == {"products": [{"name": "Kept"}]}
