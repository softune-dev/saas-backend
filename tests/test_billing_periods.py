"""Paying for 1, 3, 6 or 12 months, and a paid plan dropping to Free.

The database is production. No sweep is ever run: statements are checked on the
test's own tenant, and a dangerous state (an overdue tenant) exists only inside
a transaction that is rolled back.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import billing_actions, cache, mailer, plan_lapse, plans, queue
from app.db import SessionLocal
from app.invoices import period_label
from app.models import Invoice, Order, OrderItem, PaymentClaim, Product, Site, Tenant

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
#  Prices and dates, no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "months", "taka"),
    [
        ("starter", 1, 499), ("starter", 3, 1425), ("starter", 6, 2694), ("starter", 12, 4788),
        ("growth", 1, 1499), ("growth", 3, 4275), ("growth", 6, 8094), ("growth", 12, 14388),
        ("business", 1, 3999), ("business", 3, 11397), ("business", 6, 21594), ("business", 12, 38388),
    ],
)
def test_totals_are_the_ones_on_the_pricing_page(plan, months, taka):
    assert plans.period_price(plan, months) == taka * 100


def test_one_month_matches_the_plan_table_and_free_costs_nothing():
    for plan in plans.PAID_PLANS:
        assert plans.period_price(plan) == plans.PLANS[plan].price_cents
        assert set(plans.PERIOD_PRICES_CENTS[plan]) == set(plans.BILLING_MONTHS)
    assert plans.period_price("free", 12) == 0
    with pytest.raises(KeyError):
        plans.period_price("starter", 2)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2026, 1, 31, tzinfo=UTC), 3, datetime(2026, 4, 30, tzinfo=UTC)),
        (datetime(2026, 11, 30, tzinfo=UTC), 3, datetime(2027, 2, 28, tzinfo=UTC)),
        (datetime(2027, 8, 31, tzinfo=UTC), 6, datetime(2028, 2, 29, tzinfo=UTC)),
        (datetime(2026, 12, 15, tzinfo=UTC), 12, datetime(2027, 12, 15, tzinfo=UTC)),
        (datetime(2026, 12, 15, tzinfo=UTC), 1, datetime(2027, 1, 15, tzinfo=UTC)),
    ],
)
def test_months_are_calendar_months(start, months, expected):
    assert billing_actions.add_months(start, months) == expected


def test_same_plan_extends_from_its_own_date():
    due = datetime(2026, 12, 5, tzinfo=UTC)
    assert billing_actions.next_renewal("growth", due, NOW, new_plan="growth", months=12) == datetime(2027, 12, 5, tzinfo=UTC)
    late = datetime(2026, 11, 20, tzinfo=UTC)
    assert billing_actions.next_renewal("growth", late, NOW, new_plan="growth", months=3) == datetime(2027, 2, 20, tzinfo=UTC)


def test_free_or_trial_starts_from_now():
    for old in ("free", "trial", "demo"):
        assert billing_actions.next_renewal(old, None, NOW, new_plan="starter", months=6) == datetime(2027, 5, 26, 12, tzinfo=UTC)
        stale = datetime(2026, 5, 1, tzinfo=UTC)
        assert billing_actions.next_renewal(old, stale, NOW, new_plan="starter", months=6) == datetime(2027, 5, 26, 12, tzinfo=UTC)


def test_a_year_of_starter_cannot_become_a_year_of_business():
    year = billing_actions.add_months(NOW, 12)
    got = billing_actions.next_renewal("starter", year, NOW, new_plan="business", months=1)
    month = billing_actions.add_months(NOW, 1)
    extra = got - month
    # 365 days at 499/3999 of the value.
    assert timedelta(days=45) < extra < timedelta(days=46)


def test_moving_down_keeps_the_value_of_unused_time():
    due = NOW + timedelta(days=30)
    got = billing_actions.next_renewal("growth", due, NOW, new_plan="starter", months=1)
    assert timedelta(days=90) < got - billing_actions.add_months(NOW, 1) < timedelta(days=91)


def test_period_label():
    assert period_label(NOW, 1) == "Nov 2026"
    assert period_label(NOW, 12) == "Nov 2026, 12 months"


def test_emails_quote_the_period():
    _s, _h, text = mailer.plan_renewal_upcoming_email("A", "Starter", 4788, "Jan 1", 3, months=12)
    assert "৳4,788 for 12 months" in text and "/mo" not in text
    _s, _h, text = mailer.plan_renewal_upcoming_email("A", "Starter", 499, "Jan 1", 3)
    assert "৳499/mo" in text
    _s, _h, text = mailer.plan_payment_due_email("A", "Starter", 499, grace_days=7, to_free=True)
    assert "Free plan in 7 days" in text and "pause" not in text
    _s, _h, text = mailer.plan_payment_due_email("A", "Starter", 499, grace_days=7)
    assert "access will pause in 7 days" in text
    assert "unpaid for 30 days after that is deleted" in text and "ever deleted" not in text


# ---------------------------------------------------------------------------
#  Paying, with the database
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Nothing leaves the test: no queue, cache, or email."""
    sent: list[tuple] = []

    async def publish(job, data=None, *a, **k):
        sent.append((job, data))

    async def nothing(*a, **k):
        return True

    monkeypatch.setattr(queue, "publish", publish)
    monkeypatch.setattr(cache, "invalidate_site", nothing)
    monkeypatch.setattr(cache, "invalidate_dashboard", nothing)
    monkeypatch.setattr(mailer, "send_email", nothing)
    return sent


async def _tenant(tenant_id: str) -> Tenant:
    async with SessionLocal() as db:
        return (await db.execute(select(Tenant).where(Tenant.id == uuid.UUID(tenant_id)))).scalar_one()


async def test_a_yearly_payment_is_priced_matched_and_applied(account_factory):
    from app import claims

    acct = await account_factory("trial")
    trx = f"T{uuid.uuid4().hex[:10].upper()}"
    res = await acct.post(
        "/billing/manual-payment",
        json={"plan": "growth", "months": 12, "sender_number": "01711111111", "trx_id": trx},
    )
    assert res.status_code == 202, res.text
    async with SessionLocal() as db:
        claim = (await db.execute(select(PaymentClaim).where(PaymentClaim.trx_id == trx))).scalar_one()
        assert (claim.months, claim.amount_cents, claim.status) == (12, 1_438_800, "pending")
        claim.status = "verified"
        await db.commit()
        await claims.apply_verified_claim(db, claim)
    tenant = await _tenant(acct.tenant_id)
    assert (tenant.plan, tenant.plan_months, tenant.plan_lapsed_at) == ("growth", 12, None)
    expected = billing_actions.add_months(datetime.now(UTC), 12)
    assert abs((tenant.plan_renews_at - expected).total_seconds()) < 120
    async with SessionLocal() as db:
        invoice = (
            await db.execute(select(Invoice).where(Invoice.tenant_id == tenant.id).order_by(Invoice.issued_at.desc()).limit(1))
        ).scalars().one()
    assert (invoice.amount_cents, invoice.months) == (1_438_800, 12)
    assert invoice.period_label.endswith(", 12 months")


async def test_an_old_dashboard_tab_still_pays_one_month_and_odd_periods_are_refused(account_factory):
    acct = await account_factory("starter")
    trx = f"T{uuid.uuid4().hex[:10].upper()}"
    body = {"plan": "starter", "sender_number": "01711111111", "trx_id": trx}
    assert (await acct.post("/billing/manual-payment", json=body)).status_code == 202
    async with SessionLocal() as db:
        claim = (await db.execute(select(PaymentClaim).where(PaymentClaim.trx_id == trx))).scalar_one()
    assert (claim.months, claim.amount_cents) == (1, 49_900)
    for months in (0, 2, 24, -1):
        assert (await acct.post("/billing/manual-payment", json={**body, "months": months})).status_code == 422


async def test_me_carries_the_prices(account_factory):
    acct = await account_factory("starter")
    body = (await acct.get("/auth/me")).json()
    assert body["plan_info"]["prices"]["business"] == {"1": 399900, "3": 1139700, "6": 2159400, "12": 3838800}
    assert body["tenant"]["plan_months"] == 1 and body["tenant"]["plan_lapsed_at"] is None


# ---------------------------------------------------------------------------
#  Lapsing to Free
# ---------------------------------------------------------------------------


async def test_only_a_late_paid_tenant_lapses(account_factory):
    acct = await account_factory("growth")
    tid = uuid.UUID(acct.tenant_id)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    late = cutoff - timedelta(days=1)
    async with SessionLocal() as db:
        try:
            safe = (
                {"plan": "growth", "status": "active", "plan_renews_at": now},
                {"plan": "growth", "status": "active", "plan_renews_at": None},
                {"plan": "growth", "status": "suspended", "plan_renews_at": late},
                {"plan": "demo", "status": "active", "plan_renews_at": late},
                {"plan": "trial", "status": "active", "plan_renews_at": late},
                {"plan": "free", "status": "active", "plan_renews_at": late},
            )
            for values in safe:
                await db.execute(update(Tenant).where(Tenant.id == tid).values(**values))
                assert (await db.execute(plan_lapse.candidates_stmt(cutoff).where(Tenant.id == tid))).first() is None, values
                assert (await db.execute(plan_lapse.lapse_stmt(tid, cutoff))).first() is None, values
            for status in ("active", "payment_overdue"):
                await db.execute(
                    update(Tenant).where(Tenant.id == tid).values(plan="growth", status=status, plan_renews_at=late, plan_months=12)
                )
                assert (await db.execute(plan_lapse.candidates_stmt(cutoff).where(Tenant.id == tid))).scalar_one() == tid
                assert (await db.execute(plan_lapse.lapse_stmt(tid, cutoff))).scalar_one() == tid
                row = (
                    await db.execute(
                        select(Tenant.plan, Tenant.status, Tenant.plan_renews_at, Tenant.plan_months, Tenant.plan_lapsed_at)
                        .where(Tenant.id == tid)
                    )
                ).one()
                assert row[:4] == ("free", "active", None, 1) and row[4] is not None
        finally:
            await db.rollback()


async def test_lapsing_does_nothing_while_the_switch_is_off(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("must not look for tenants")

    monkeypatch.setattr(plan_lapse.settings, "free_plan_enabled", False)
    monkeypatch.setattr(plan_lapse, "candidates_stmt", forbidden)
    await plan_lapse.lapse_overdue(NOW)


async def _products(acct, n: int, *, active: bool = True) -> list[uuid.UUID]:
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(acct.site_id)))).scalar_one()
        rows = [
            Product(
                site_id=site.id, tenant_id=site.tenant_id, name=f"P{i}", slug=f"p-{uuid.uuid4().hex[:8]}",
                price_cents=1000, currency="BDT", is_active=active,
            )
            for i in range(n)
        ]
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def _hidden(acct) -> set[uuid.UUID]:
    async with SessionLocal() as db:
        return set(
            (
                await db.execute(
                    select(Product.id).where(Product.tenant_id == uuid.UUID(acct.tenant_id), Product.plan_hidden)
                )
            ).scalars()
        )


async def test_best_sellers_stay_and_paying_again_shows_everything(account_factory):
    acct = await account_factory("growth")
    other = await account_factory("growth")
    tid = uuid.UUID(acct.tenant_id)
    ids = await _products(acct, 13)
    theirs = await _products(other, 12)
    best = ids[:3]
    async with SessionLocal() as db:
        site_id = uuid.UUID(acct.site_id)
        for n, pid in enumerate(best):
            order = Order(
                site_id=site_id, tenant_id=tid, order_number=f"T-{n}", customer={}, subtotal_cents=1000,
                total_cents=1000, currency="BDT",
            )
            order.items = [
                OrderItem(product_id=pid, name_snapshot="P", quantity=5 + n, unit_price_cents=1000, total_cents=1000 * (5 + n))
            ]
            db.add(order)
        await db.commit()
        await plan_lapse.hide_extra_products(db, tid)
    hidden = await _hidden(acct)
    assert len(hidden) == 3 and not hidden & set(best)
    assert await _hidden(other) == set(), "another tenant's products are never touched"

    await plan_lapse.restore(tid)
    assert await _hidden(acct) == set()
    assert len(theirs) == 12


async def test_a_hidden_product_is_not_on_the_store_or_sellable_but_stays_in_the_dashboard(account_factory):
    acct = await account_factory("starter")
    (pid,) = await _products(acct, 1)
    host = (await acct.post(f"/sites/{acct.site_id}/publish", json={})).json()["subdomain"]
    async with SessionLocal() as db:
        await db.execute(update(Product).where(Product.id == pid).values(plan_hidden=True, stock=5))
        await db.commit()
        slug = (await db.execute(select(Product.slug).where(Product.id == pid))).scalar_one()
    listing = (await acct.client.get(f"/public/site/{host}/products")).json()
    items = listing["items"] if isinstance(listing, dict) else listing
    assert str(pid) not in [p["id"] for p in items]
    assert (await acct.client.get(f"/public/site/{host}/products/{slug}")).status_code == 404
    order = await acct.client.post(
        f"/public/site/{host}/orders",
        json={"customer": {"first_name": "A", "phone": "01711111111"}, "items": [{"product_id": str(pid), "quantity": 1}]},
    )
    assert order.status_code in (400, 404, 409, 422), order.text
    mine = (await acct.get(f"/sites/{acct.site_id}/products")).json()["items"]
    assert [p["plan_hidden"] for p in mine if p["id"] == str(pid)] == [True]
    # A merchant can't switch it back on by hand.
    patched = await acct.patch(f"/sites/{acct.site_id}/products/{pid}", json={"plan_hidden": False})
    assert patched.status_code in (200, 422) and pid in await _hidden(acct)


async def test_switching_products_on_free_cannot_pass_the_limit(account_factory):
    acct = await account_factory("free")
    ids = await _products(acct, 10)
    spare = (await _products(acct, 1, active=False))[0]
    url = f"/sites/{acct.site_id}/products"
    assert (await acct.patch(f"{url}/{spare}", json={"is_active": True})).status_code == 200
    assert len(await _hidden(acct)) == 1
    # Deleting a visible one makes room for the hidden one.
    hidden = await _hidden(acct)
    visible = next(i for i in [*ids, spare] if i not in hidden)
    assert (await acct.delete(f"{url}/{visible}")).status_code == 204
    assert await _hidden(acct) == set()


async def test_a_custom_domain_is_released_after_the_grace_period_and_asked_for_again(account_factory, quiet):
    acct = await account_factory("growth")
    tid, sid = uuid.UUID(acct.tenant_id), uuid.UUID(acct.site_id)
    domain = f"t{uuid.uuid4().hex[:8]}.example.com"
    now = datetime.now(UTC)
    cutoff = now - plan_lapse.DOMAIN_GRACE
    async with SessionLocal() as db:
        try:
            await db.execute(update(Site).where(Site.id == sid).values(custom_domain=domain, status="published"))
            for plan, lapsed in (("growth", cutoff - timedelta(days=1)), ("free", None), ("free", now)):
                await db.execute(update(Tenant).where(Tenant.id == tid).values(plan=plan, plan_lapsed_at=lapsed))
                assert (await db.execute(plan_lapse.release_domain_stmt(sid, cutoff))).first() is None, (plan, lapsed)
            await db.execute(update(Tenant).where(Tenant.id == tid).values(plan="free", plan_lapsed_at=cutoff - timedelta(days=1)))
            assert (await db.execute(plan_lapse.release_domain_stmt(sid, cutoff))).scalar_one() == sid
            row = (await db.execute(select(Site.custom_domain, Site.pending_custom_domain).where(Site.id == sid))).one()
            assert tuple(row) == (None, domain)
        finally:
            await db.rollback()

    async with SessionLocal() as db:
        await db.execute(update(Site).where(Site.id == sid).values(pending_custom_domain=domain, status="published"))
        await db.commit()
        await plan_lapse.restore(tid)
        await db.execute(update(Site).where(Site.id == sid).values(pending_custom_domain=None))
        await db.commit()
    assert (queue.JOB_ATTACH_DOMAIN, {"site_id": acct.site_id, "domain": domain}) in quiet
