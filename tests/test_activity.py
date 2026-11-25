"""Activity tracking (tenants.last_active_at) and the guards that keep the
paid-account deletion lifecycle away from free stores.

The database is production, so no worker sweep is ever called here. The
candidate queries are tested directly, narrowed to this test's own tenant, and
the dangerous state (payment_overdue + an old plan_overdue_since) only ever
exists inside a transaction that is rolled back: the real worker cannot see it.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql

from app import activity, billing_actions, plans, worker
from app.db import SessionLocal
from app.models import Tenant, User
from app.security import create_access_token, create_refresh_token
from tests import test_mcp_oauth

OLD = timedelta(days=2)


@pytest.fixture(autouse=True)
def _fresh_throttle(monkeypatch):
    activity._attempted.clear()
    # A slow link to the database must not look like a product failure.
    monkeypatch.setattr(activity, "TIMEOUT_SECONDS", 60)
    yield
    activity._attempted.clear()


async def _age(account, by: timedelta = OLD) -> datetime:
    """Back-date this test's own tenant; returns the value now stored."""
    async with SessionLocal() as db:
        await db.execute(
            update(Tenant).where(Tenant.id == uuid.UUID(account.tenant_id))
            .values(last_active_at=func.now() - by)
        )
        await db.commit()
    activity._attempted.clear()
    return await _last_active(account)


async def _last_active(account) -> datetime:
    async with SessionLocal() as db:
        return (
            await db.execute(select(Tenant.last_active_at).where(Tenant.id == uuid.UUID(account.tenant_id)))
        ).scalar_one()


async def _owner(account) -> User:
    async with SessionLocal() as db:
        return (
            await db.execute(select(User).where(User.tenant_id == uuid.UUID(account.tenant_id)))
        ).scalars().one()


def _is_recent(value: datetime) -> bool:
    return datetime.now(UTC) - value < timedelta(minutes=10)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# --- no database ------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, automatic",
    [
        ("/sites/abc/notifications", True),
        ("/sites/abc/notifications/", True),
        ("/api/sites/abc/notifications", True),
        ("/ai/usage", True),
        ("/api/ai/usage/", True),
        ("/sites/abc/pay/devices", True),
        ("/sites/abc/pay/payments", True),
        # Real use that merely looks similar.
        ("/sites/abc/notifications/read-all", False),
        ("/sites/abc/pay/payments/123", False),
        ("/ai/usage/history", False),
        ("/auth/me", False),
        ("/sites/abc/products", False),
        ("/sites/abc/orders", False),
        ("/sites", False),
        ("", False),
    ],
)
def test_automatic_polling_is_recognised(path, automatic):
    assert activity.is_automatic(path) is automatic


def test_the_update_only_writes_last_active_at_and_only_when_stale():
    sql = _sql(activity.touch_stmt(uuid.uuid4()))
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    assert set_clause.strip() == "last_active_at=now()"
    assert "tenants.last_active_at < now() -" in sql
    assert "SKIP LOCKED" in sql


async def test_throttle_is_hourly_per_tenant_and_bounded(monkeypatch):
    writes = []

    async def fake(tenant_id):
        writes.append(tenant_id)

    clock = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(activity, "_write", fake)
    # The module's own name only: patching time.monotonic itself would stop
    # the event loop's clock.
    monkeypatch.setattr(activity, "time", SimpleNamespace(monotonic=lambda: clock.t))
    monkeypatch.setattr(activity, "MAX_TRACKED", 3)
    a, b = uuid.uuid4(), uuid.uuid4()
    await activity.touch(a)
    await activity.touch(a)
    await activity.touch(b)
    assert writes == [a, b]
    clock.t += 3599
    await activity.touch(a)
    assert writes == [a, b]
    clock.t += 2
    await activity.touch(a)
    assert writes == [a, b, a]
    for _ in range(50):
        await activity.touch(uuid.uuid4())
    assert len(activity._attempted) == 3


async def test_a_failure_is_swallowed_and_not_retried_this_hour(monkeypatch, caplog):
    calls = []

    async def broken(tenant_id):
        calls.append(tenant_id)
        raise RuntimeError("database is down")

    monkeypatch.setattr(activity, "_write", broken)
    tenant = uuid.uuid4()
    await activity.touch(tenant)
    await activity.touch(tenant)
    assert calls == [tenant]
    assert "record activity" in caplog.text
    assert activity._in_flight == 0


async def test_a_stuck_database_is_cut_off(monkeypatch):
    async def stuck(tenant_id):
        await asyncio.sleep(30)

    monkeypatch.setattr(activity, "_write", stuck)
    monkeypatch.setattr(activity, "TIMEOUT_SECONDS", 0.05)
    await asyncio.wait_for(activity.touch(uuid.uuid4()), timeout=2)
    assert activity._in_flight == 0


async def test_only_a_few_run_at_once_and_the_rest_are_not_lost(monkeypatch):
    release = asyncio.Event()
    running = []

    async def slow(tenant_id):
        running.append(tenant_id)
        await release.wait()

    monkeypatch.setattr(activity, "_write", slow)
    ids = [uuid.uuid4() for _ in range(10)]
    tasks = [asyncio.create_task(activity.touch(i)) for i in ids]
    await asyncio.sleep(0.05)
    assert len(running) == activity.MAX_IN_FLIGHT
    release.set()
    await asyncio.gather(*tasks)
    skipped = [i for i in ids if i not in running]
    assert all(activity.due(i) for i in skipped)  # tried again on their next request


NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("plan", plans.PAID_PLANS)
@pytest.mark.parametrize(
    "renews_at, expected",
    [
        # Paid early, on the day, and a few days late: the anchor never drifts.
        (datetime(2026, 11, 30, 6, 0, tzinfo=UTC), datetime(2026, 12, 30, 6, 0, tzinfo=UTC)),
        (NOW, datetime(2026, 12, 24, 9, 0, tzinfo=UTC)),
        (datetime(2026, 11, 20, 6, 0, tzinfo=UTC), datetime(2026, 12, 20, 6, 0, tzinfo=UTC)),
        # Very late but still a paying customer: exactly as before too.
        (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
        (None, datetime(2026, 12, 24, 9, 0, tzinfo=UTC)),
    ],
)
def test_a_paying_tenant_keeps_its_anchor(plan, renews_at, expected):
    assert billing_actions.next_renewal(plan, renews_at, NOW) == expected


@pytest.mark.parametrize("plan", ["free", "trial", "demo"])
def test_a_tenant_coming_back_to_paid_starts_from_now(plan):
    stale = datetime(2026, 3, 10, tzinfo=UTC)
    expected = datetime(2026, 12, 24, 9, 0, tzinfo=UTC)
    assert billing_actions.next_renewal(plan, stale, NOW) == expected
    assert billing_actions.next_renewal(plan, None, NOW) == expected
    # Time already paid for is never taken away.
    future = datetime(2026, 12, 5, tzinfo=UTC)
    assert billing_actions.next_renewal(plan, future, NOW) == datetime(2027, 1, 5, tzinfo=UTC)


@pytest.mark.parametrize("old_plan, from_now", [("free", True), ("starter", False)])
async def test_apply_paid_plan_judges_the_plan_the_tenant_had_before(monkeypatch, old_plan, from_now):
    """Everything with a side effect is faked: no row, invoice, job or email."""
    published = []

    async def fake_save(db, obj):
        return obj

    async def fake_number(db, tenant_id):
        return "INV-TEST"

    async def fake_publish(job, payload):
        published.append(job)

    monkeypatch.setattr(billing_actions.crud, "save", fake_save)
    monkeypatch.setattr(billing_actions.crud, "next_invoice_number", fake_number)
    monkeypatch.setattr(billing_actions.queue, "publish", fake_publish)

    async def fake_restore(tenant_id):
        published.append("restore")

    monkeypatch.setattr(billing_actions.plan_lapse, "restore", fake_restore)

    async def same_tenant(db, tenant_id):
        return tenant

    monkeypatch.setattr(billing_actions, "lock_tenant", same_tenant)
    stale = datetime(2026, 3, 10, tzinfo=UTC)
    tenant = Tenant(
        id=uuid.uuid4(), plan=old_plan, status="payment_overdue",
        plan_renews_at=stale, plan_overdue_since=stale,
    )
    invoice = await billing_actions.apply_paid_plan(None, tenant, "growth")
    assert tenant.plan == "growth" and tenant.status == "active" and tenant.plan_overdue_since is None
    assert invoice.plan == "growth" and len(published) == 2
    if from_now:
        assert tenant.plan_renews_at > datetime.now(UTC) + timedelta(days=27)
    else:
        assert tenant.plan_renews_at == datetime(2026, 4, 10, tzinfo=UTC)


# --- through the API --------------------------------------------------------------

async def test_a_request_moves_an_old_last_active_at(account):
    await _age(account)
    assert (await account.get("/auth/me")).status_code == 200
    assert _is_recent(await _last_active(account))


async def test_a_second_request_within_the_hour_writes_nothing(account):
    await _age(account)
    await account.get("/auth/me")
    first = await _last_active(account)
    await account.get("/auth/me")
    assert await _last_active(account) == first
    # Another process (an empty throttle) does not write again either.
    half_hour = await _age(account, timedelta(minutes=30))
    await account.get("/auth/me")
    assert await _last_active(account) == half_hour


async def test_polling_does_not_count_but_the_next_real_request_does(account):
    before = await _age(account)
    for path in (
        f"/sites/{account.site_id}/notifications",
        f"/sites/{account.site_id}/notifications?limit=5",
        f"/sites/{account.site_id}/pay/devices",
        f"/sites/{account.site_id}/pay/payments?status=waiting",
        "/ai/usage",
    ):
        res = await account.get(path)
        assert res.status_code < 500, (path, res.text)
    assert await _last_active(account) == before
    await account.get("/auth/me")
    assert _is_recent(await _last_active(account))


async def test_clicking_in_the_notification_bell_is_real_use(account):
    await _age(account)
    assert (await account.post(f"/sites/{account.site_id}/notifications/read-all")).status_code == 204
    assert _is_recent(await _last_active(account))


async def test_a_database_error_never_fails_the_request(account, monkeypatch):
    before = await _age(account)

    def broken():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(activity, "SessionLocal", broken)
    assert (await account.get("/auth/me")).status_code == 200
    assert not activity.due(uuid.UUID(account.tenant_id))  # it really was attempted
    assert await _last_active(account) == before


async def test_a_superadmin_session_is_not_the_merchant_being_active(account):
    before = await _age(account)
    owner = await _owner(account)
    token = create_access_token(owner.id, owner.tenant_id, owner.role, True)
    res = await account.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert await _last_active(account) == before


async def test_a_bad_token_touches_nothing(client, monkeypatch):
    called = []

    async def spy(tenant_id, *, path=""):
        called.append(tenant_id)

    monkeypatch.setattr(activity, "touch", spy)
    res = await client.get("/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert res.status_code == 401 and called == []


async def test_the_chatgpt_connector_counts(account):
    tokens = await test_mcp_oauth._tokens(account)
    await _age(account)
    res = await test_mcp_oauth.rpc(account, tokens["access_token"], "tools/list")
    assert res.status_code == 200, res.text
    assert _is_recent(await _last_active(account))


async def test_token_refresh_records_a_login_but_not_activity(account):
    """A refresh happens by itself in an idle tab (a poll gets a 401), so it
    says the session is alive, not that anyone is using the shop."""
    before = await _age(account)
    owner = await _owner(account)
    assert owner.last_login_at is None
    res = await account.client.post(
        "/auth/refresh",
        json={"refresh_token": create_refresh_token(owner.id, owner.tenant_id, owner.role)},
    )
    assert res.status_code == 200, res.text
    assert _is_recent((await _owner(account)).last_login_at)
    assert await _last_active(account) == before


@pytest.mark.parametrize("age, rewritten", [(timedelta(hours=1), False), (timedelta(hours=7), True)])
async def test_refresh_rewrites_last_login_only_when_hours_old(account, age, rewritten):
    owner = await _owner(account)
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == owner.id).values(last_login_at=func.now() - age))
        await db.commit()
    before = (await _owner(account)).last_login_at
    res = await account.client.post(
        "/auth/refresh",
        json={"refresh_token": create_refresh_token(owner.id, owner.tenant_id, owner.role)},
    )
    assert res.status_code == 200, res.text
    after = (await _owner(account)).last_login_at
    if rewritten:
        assert after > before and _is_recent(after)
    else:
        assert after == before


async def test_finishing_signup_records_a_login(client, monkeypatch):
    from app import crud
    from app.api import trial
    from tests.conftest import _cleanup

    suffix = uuid.uuid4().hex[:12]
    email = f"test-{suffix}@softune-test-fixtures.dev"

    async def pending(signup_token, **kw):
        return {
            "email": email, "password_hash": "x", "full_name": "Test User", "email_verified": True,
            "shop_name": f"test-fixture-{suffix}", "phone": "01700000000", "category": None, "tagline": None,
        }

    async def nothing(*args, **kw):
        return None

    monkeypatch.setattr(trial, "_load_pending", pending)
    monkeypatch.setattr(trial.queue, "publish", nothing)
    monkeypatch.setattr(trial.cache, "drop", nothing)
    monkeypatch.setattr(trial.cache, "invalidate_site", nothing)
    made = []
    real_create = crud.create_tenant_owner_and_site

    async def remember(*args, **kw):
        user, site = await real_create(*args, **kw)
        made.append(str(user.tenant_id))
        return user, site

    monkeypatch.setattr(trial.crud, "create_tenant_owner_and_site", remember)
    try:
        res = await client.post("/trial/complete", json={"signup_token": "x", "template_key": "aurora"})
        assert res.status_code == 200, res.text
        assert res.json()["access_token"]
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        assert user.last_login_at is not None and _is_recent(user.last_login_at)
    finally:
        await _cleanup(made)


# --- the paid-account deletion lifecycle ------------------------------------------

CUTOFF = timedelta(days=30)


async def _overdue(db, tid: uuid.UUID, plan: str, **values) -> None:
    """The state the sweeps look for, on this test's own tenant. Never committed."""
    await db.execute(
        update(Tenant).where(Tenant.id == tid).values(
            **{
                "plan": plan, "status": "payment_overdue", "plan_deletion_warned_at": None,
                "plan_overdue_since": datetime.now(UTC) - timedelta(days=90), **values,
            }
        ).execution_options(synchronize_session=False)
    )


async def _selected(db, stmt, tid: uuid.UUID) -> bool:
    return (await db.execute(stmt.where(Tenant.id.in_([tid])))).first() is not None


async def test_only_paid_plans_are_warned_or_selected_for_deletion(account):
    tid = uuid.UUID(account.tenant_id)
    cutoff = datetime.now(UTC) - CUTOFF
    async with SessionLocal() as db:
        try:
            for plan in plans.PLANS:
                await _overdue(db, tid, plan)
                expected = plan in plans.PAID_PLANS
                assert await _selected(db, worker.deletion_warning_candidates_stmt(cutoff), tid) is expected, plan
                assert await _selected(db, worker.deletion_candidates_stmt(cutoff), tid) is expected, plan
        finally:
            await db.rollback()


async def test_the_paid_filter_is_otherwise_unchanged(account):
    tid = uuid.UUID(account.tenant_id)
    now = datetime.now(UTC)
    cutoff = now - CUTOFF
    async with SessionLocal() as db:
        try:
            for values in (
                {"status": "active"},
                {"status": "suspended"},
                {"plan_overdue_since": None},
                {"plan_overdue_since": now - timedelta(days=5)},
            ):
                await _overdue(db, tid, "growth", **values)
                assert not await _selected(db, worker.deletion_warning_candidates_stmt(cutoff), tid), values
                assert not await _selected(db, worker.deletion_candidates_stmt(cutoff), tid), values
            # Warned once already: no second warning, but still due for deletion.
            await _overdue(db, tid, "growth", plan_deletion_warned_at=now)
            assert not await _selected(db, worker.deletion_warning_candidates_stmt(cutoff), tid)
            assert await _selected(db, worker.deletion_candidates_stmt(cutoff), tid)
        finally:
            await db.rollback()


async def test_the_delete_itself_rechecks_so_a_late_change_survives(account):
    """Selected while on a paid plan, then changed before the DELETE runs."""
    tid = uuid.UUID(account.tenant_id)
    cutoff = datetime.now(UTC) - CUTOFF
    async with SessionLocal() as db:
        try:
            for plan, values in (("free", {}), ("trial", {}), ("demo", {}), ("growth", {"status": "active"})):
                await _overdue(db, tid, plan, **values)
                deleted = (await db.execute(worker.delete_abandoned_stmt([tid], cutoff))).scalars().all()
                assert deleted == [], (plan, values)
            await _overdue(db, tid, "growth")
            deleted = (await db.execute(worker.delete_abandoned_stmt([tid], cutoff))).scalars().all()
            assert deleted == [tid]
        finally:
            await db.rollback()
    assert (await account.get("/auth/me")).status_code == 200  # the rollback restored everything


async def test_expired_trial_delete_rechecks_the_plan(account):
    tid = uuid.UUID(account.tenant_id)
    cutoff = datetime.now(UTC) - CUTOFF
    old = cutoff - timedelta(days=1)
    async with SessionLocal() as db:
        try:
            for plan in ("free", "starter", "demo"):
                await db.execute(update(Tenant).where(Tenant.id == tid).values(plan=plan, trial_expires_at=old))
                assert (await db.execute(worker.delete_expired_trials_stmt([tid], cutoff))).scalars().all() == [], plan
            await db.execute(update(Tenant).where(Tenant.id == tid).values(plan="trial", trial_expires_at=old))
            assert (await db.execute(worker.delete_expired_trials_stmt([tid], cutoff))).scalars().all() == [tid]
        finally:
            await db.rollback()


async def test_overdue_pause_rechecks_so_a_late_payment_is_not_paused(account):
    tid = uuid.UUID(account.tenant_id)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=3)
    late = cutoff - timedelta(days=1)
    async with SessionLocal() as db:
        try:
            for plan, renews in (("free", late), ("trial", late), ("growth", now), ("growth", None)):
                await db.execute(update(Tenant).where(Tenant.id == tid).values(plan=plan, status="active", plan_renews_at=renews))
                assert (await db.execute(worker.pause_overdue_stmt(tid, cutoff))).first() is None, (plan, renews)
            await db.execute(update(Tenant).where(Tenant.id == tid).values(plan="growth", status="active", plan_renews_at=late))
            assert (await db.execute(worker.pause_overdue_stmt(tid, cutoff))).scalar_one() == tid
        finally:
            await db.rollback()
