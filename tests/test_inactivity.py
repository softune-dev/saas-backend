"""Deleting unused Free accounts. Deletion cannot be undone, so each rule has a
test of its own.

SAFETY: these run against the live database. The job is only ever called through
`sweep`, which insists on a list of this test's own tenants, and email, Vercel,
Cloudinary and the cache are faked for every test.

Needs migrations/090_inactivity.sql.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import account_archive, contacts, inactivity, plans, worker
from app.config import settings
from app.db import SessionLocal
from app.models import DeletedAccount, Invoice, PaymentClaim, Product, Site, Tenant, TenantInactivity, User

DAY = timedelta(days=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


# --- the rules, with no database ------------------------------------------------


def _warned(stage: int, on_day: float, for_activity: datetime = T0) -> inactivity.Warned:
    return inactivity.Warned(stage, T0 + on_day * DAY, for_activity)


@pytest.mark.parametrize(
    "day, paid, warned, expected",
    [
        # never paid: warnings on 15, 23, 29, gone on 30
        (14.9, False, None, None),
        (15, False, None, "warn 1"),
        (22.9, False, _warned(1, 15), None),
        (23, False, _warned(1, 15), "warn 2"),
        (28.9, False, _warned(2, 23), None),
        (29, False, _warned(2, 23), "warn 3"),
        (29.9, False, _warned(3, 28), None),  # not 30 days yet
        (30, False, _warned(3, 29), "delete"),
        # paid before: 60, 75, 85, 90
        (30, True, None, None),
        (59.9, True, None, None),
        (60, True, None, "warn 1"),
        (75, True, _warned(1, 60), "warn 2"),
        (85, True, _warned(2, 75), "warn 3"),
        (89.9, True, _warned(3, 85), None),
        (90, True, _warned(3, 85), "delete"),
    ],
)
def test_the_schedule(day, paid, warned, expected):
    assert inactivity.next_step(T0 + day * DAY, T0, paid, warned) == expected


def test_a_worker_that_was_down_moves_the_warnings_later_instead_of_squeezing_them():
    late = T0 + 200 * DAY
    # Long overdue, but nothing sent yet: only the first warning.
    assert inactivity.next_step(late, T0, False, None) == "warn 1"
    # The next ones wait their five days.
    assert inactivity.next_step(late + 4.9 * DAY, T0, False, _warned(1, 200)) is None
    assert inactivity.next_step(late + 5 * DAY, T0, False, _warned(1, 200)) == "warn 2"
    assert inactivity.next_step(late + 4.9 * DAY, T0, False, _warned(2, 200)) is None


def test_deletion_needs_the_last_warning_to_be_a_day_old():
    now = T0 + 100 * DAY
    assert inactivity.next_step(now, T0, False, _warned(3, 99.5)) is None
    assert inactivity.next_step(now, T0, False, _warned(3, 99)) == "delete"


def test_two_warnings_are_never_enough():
    assert inactivity.next_step(T0 + 400 * DAY, T0, False, _warned(2, 390)) == "warn 3"


def test_any_activity_voids_the_earlier_warnings():
    came_back = T0 + 40 * DAY
    old = _warned(3, 29, for_activity=T0)
    assert inactivity.next_step(came_back + 14 * DAY, came_back, False, old) is None
    assert inactivity.next_step(came_back + 15 * DAY, came_back, False, old) == "warn 1"


def test_the_periods_are_the_ones_promised_to_merchants():
    assert (plans.FREE_INACTIVE_DAYS, plans.FORMER_PAID_INACTIVE_DAYS) == (30, 90)
    assert inactivity.WARNING_DAYS == {30: (15, 23, 29), 90: (60, 75, 85)}
    assert inactivity.MAX_DELETIONS == 20


def test_the_job_is_off_unless_the_server_says_otherwise():
    assert type(settings).model_fields["inactivity_deletion"].default == "off"
    assert hasattr(worker, "inactivity_loop")


# --- the job, on this test's own tenants -----------------------------------------


class Outbox:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.works = True

    async def __call__(self, to, subject, html_body, text_body, *a, **kw):
        if self.works:
            self.sent.append((to, subject))
        return self.works


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(inactivity.mailer, "send_email", box)
    return box


@pytest.fixture(autouse=True)
def cleaned(monkeypatch):
    """Nothing outside the database is touched: records what would have been."""
    calls: list[uuid.UUID] = []

    async def fake(site):
        calls.append(site.tenant_id)

    monkeypatch.setattr(inactivity, "_clean_up", fake)
    return calls


async def sweep(accounts, *, days: float, mode: str = "on") -> inactivity.Report:
    """The ONLY way these tests run the job: always limited to their tenants.
    `days` is how long after the account's last activity the run happens."""
    assert accounts, "a sweep in tests must name its tenants"
    ids = [uuid.UUID(a.tenant_id) for a in accounts]
    async with SessionLocal() as db:
        last = (await db.execute(select(Tenant.last_active_at).where(Tenant.id == ids[0]))).scalar_one()
    return await inactivity.run(mode=mode, tenant_ids=ids, now=last + days * DAY)


async def _same_clock(accounts) -> None:
    """Gives several accounts one activity time, so one `now` suits them all."""
    async with SessionLocal() as db:
        await db.execute(
            update(Tenant)
            .where(Tenant.id.in_([uuid.UUID(a.tenant_id) for a in accounts]))
            .values(last_active_at=datetime.now(UTC))
        )
        await db.commit()


async def _exists(account) -> bool:
    async with SessionLocal() as db:
        return await db.get(Tenant, uuid.UUID(account.tenant_id)) is not None


async def _stage(account) -> int:
    async with SessionLocal() as db:
        row = await db.get(TenantInactivity, uuid.UUID(account.tenant_id))
        return row.stage if row else 0


async def _ready_to_delete(accounts, outbox) -> None:
    """Walks the accounts through all three warnings."""
    for day in (15, 23, 29):
        await sweep(accounts, days=day)


async def test_three_warnings_then_deletion(account_factory, outbox, cleaned):
    acct = await account_factory("free")

    for day, stage in ((15, 1), (23, 2), (29, 3)):
        report = await sweep([acct], days=day)
        assert report.warned == [uuid.UUID(acct.tenant_id)] and report.deleted == []
        assert await _stage(acct) == stage and await _exists(acct)

    report = await sweep([acct], days=30.5)

    assert report.deleted == [uuid.UUID(acct.tenant_id)]
    assert not await _exists(acct)
    assert cleaned == [uuid.UUID(acct.tenant_id)]
    assert len(outbox.sent) == 3
    assert outbox.sent[2][1].startswith("Last warning")


async def test_the_same_day_twice_sends_one_email(account_factory, outbox):
    acct = await account_factory("free")

    await sweep([acct], days=15)
    await sweep([acct], days=15.04)

    assert len(outbox.sent) == 1 and await _stage(acct) == 1


async def test_a_warning_that_fails_to_send_does_not_count(account_factory, outbox):
    acct = await account_factory("free")
    outbox.works = False

    for day in (15, 23, 29, 60):
        report = await sweep([acct], days=day)
        assert report.warned == [] and report.deleted == []

    assert await _stage(acct) == 0 and await _exists(acct)


@pytest.mark.parametrize("plan", ["starter", "growth", "business", "demo", "trial"])
async def test_only_free_accounts_are_ever_touched(account_factory, outbox, plan):
    acct = await account_factory(plan)

    report = await sweep([acct], days=500)

    assert (report.warned, report.deleted, outbox.sent) == ([], [], [])
    assert await _exists(acct)


@pytest.mark.parametrize("status", ["suspended", "payment_overdue"])
async def test_an_account_that_is_not_active_is_left_alone(account_factory, outbox, status):
    acct = await account_factory("free")
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(acct.tenant_id)).values(status=status))
        await db.commit()

    report = await sweep([acct], days=500)

    assert (report.warned, report.deleted, outbox.sent) == ([], [], [])


async def test_a_superadmin_account_is_never_touched(account_factory, outbox):
    acct = await account_factory("free")
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.tenant_id == uuid.UUID(acct.tenant_id)).values(is_superadmin=True))
        await db.commit()

    report = await sweep([acct], days=500)

    assert (report.warned, report.deleted, outbox.sent) == ([], [], [])


def _claim(tenant_id: uuid.UUID, status: str) -> PaymentClaim:
    return PaymentClaim(
        tenant_id=tenant_id, kind="plan", plan="starter", amount_cents=49_900, months=1,
        sender_number="01700000000", trx_id=f"T{uuid.uuid4().hex[:10].upper()}", status=status,
    )


async def test_money_waiting_to_be_confirmed_stops_everything(account_factory, outbox):
    acct = await account_factory("free")
    await _ready_to_delete([acct], outbox)
    async with SessionLocal() as db:
        db.add(_claim(uuid.UUID(acct.tenant_id), "pending"))
        await db.commit()

    report = await sweep([acct], days=45)

    assert report.deleted == [] and await _exists(acct)


@pytest.mark.parametrize("evidence", ["invoice", "verified claim", "renewal date"])
async def test_anyone_who_ever_paid_gets_ninety_days(account_factory, outbox, evidence):
    acct = await account_factory("free")
    tid = uuid.UUID(acct.tenant_id)
    async with SessionLocal() as db:
        if evidence == "invoice":
            db.add(Invoice(
                tenant_id=tid, invoice_number=f"TEST-{uuid.uuid4().hex[:10]}", plan="starter", amount_cents=49_900,
                months=1, currency="BDT", period_label="test", tenant_business_snapshot={},
            ))
        elif evidence == "verified claim":
            db.add(_claim(tid, "verified"))
        else:
            await db.execute(update(Tenant).where(Tenant.id == tid).values(plan_renews_at=datetime.now(UTC) - 40 * DAY))
        await db.commit()

    early = await sweep([acct], days=45)
    on_time = await sweep([acct], days=60)

    assert (early.warned, early.deleted) == ([], [])
    assert on_time.warned == [tid]


async def test_coming_back_starts_the_count_again(account_factory, outbox):
    acct = await account_factory("free")
    await _ready_to_delete([acct], outbox)
    await _same_clock([acct])  # they logged in: the activity time moved

    report = await sweep([acct], days=30)

    assert report.deleted == [] and report.warned == [uuid.UUID(acct.tenant_id)]
    assert await _stage(acct) == 1


async def test_report_mode_changes_and_sends_nothing(account_factory, outbox):
    acct = await account_factory("free")

    report = await sweep([acct], days=15, mode="report")

    assert report.would == [(uuid.UUID(acct.tenant_id), "warn 1")]
    assert (report.warned, report.deleted, outbox.sent) == ([], [], [])
    assert await _stage(acct) == 0


@pytest.mark.parametrize("mode", ["off", "OFF", "yes", "true", "delete"])
async def test_off_and_unknown_values_do_nothing(account_factory, outbox, mode):
    acct = await account_factory("free")

    report = await sweep([acct], days=500, mode=mode)

    assert (report.warned, report.deleted, report.would, outbox.sent) == ([], [], [], [])


async def test_an_empty_list_of_tenants_means_nobody(outbox):
    report = await inactivity.run(mode="on", tenant_ids=[], now=datetime.now(UTC) + 500 * DAY)
    assert (report.warned, report.deleted, outbox.sent) == ([], [], [])


async def test_too_many_at_once_deletes_none_and_tells_the_owner(account_factory, outbox, monkeypatch):
    first, second = await account_factory("free"), await account_factory("free")
    await _same_clock([first, second])
    await _ready_to_delete([first, second], outbox)
    outbox.sent.clear()
    monkeypatch.setattr(inactivity, "MAX_DELETIONS", 1)
    monkeypatch.setattr(inactivity, "_alerted_on", None)

    report = await sweep([first, second], days=31)
    again = await sweep([first, second], days=31.04)

    assert report.capped and report.deleted == [] and again.deleted == []
    assert await _exists(first) and await _exists(second)
    # One alert a day, not one an hour.
    assert [to for to, _ in outbox.sent] == [settings.billing_notify_email]


async def test_nothing_is_deleted_if_contacts_cannot_be_saved(account_factory, outbox, monkeypatch):
    acct = await account_factory("free")
    await _ready_to_delete([acct], outbox)

    async def broken(*a, **kw):
        raise RuntimeError("contacts table unavailable")

    monkeypatch.setattr(contacts, "upsert_tenants", broken)

    report = await sweep([acct], days=31)

    assert report.deleted == [] and await _exists(acct)


@pytest.mark.parametrize("change", ["came back", "paid", "claim sent", "suspended"])
async def test_the_delete_itself_checks_again(account_factory, outbox, change):
    """Chosen for deletion, then changed before the DELETE runs."""
    acct = await account_factory("free")
    tid = uuid.UUID(acct.tenant_id)
    await _ready_to_delete([acct], outbox)
    async with SessionLocal() as db:
        seen = (await db.execute(select(Tenant.last_active_at).where(Tenant.id == tid))).scalar_one()
    later = seen + 31 * DAY
    async with SessionLocal() as db:
        try:
            if change == "claim sent":
                db.add(_claim(tid, "pending"))
                await db.flush()
            else:
                values = {
                    "came back": {"last_active_at": seen + DAY},
                    "paid": {"plan": "growth"},
                    "suspended": {"status": "suspended"},
                }[change]
                await db.execute(update(Tenant).where(Tenant.id == tid).values(**values))
            gone = (await db.execute(inactivity.delete_inactive_stmt(tid, seen, 30, later))).scalars().all()
            assert gone == []
        finally:
            await db.rollback()


async def test_the_delete_refuses_without_three_recorded_warnings(account_factory, outbox):
    acct = await account_factory("free")
    tid = uuid.UUID(acct.tenant_id)
    await sweep([acct], days=15)
    await sweep([acct], days=23)
    async with SessionLocal() as db:
        seen = (await db.execute(select(Tenant.last_active_at).where(Tenant.id == tid))).scalar_one()
        try:
            gone = (await db.execute(inactivity.delete_inactive_stmt(tid, seen, 30, seen + 90 * DAY))).scalars().all()
            assert gone == []
        finally:
            await db.rollback()


# --- the record kept after a deletion --------------------------------------------


async def _records(account) -> list[DeletedAccount]:
    async with SessionLocal() as db:
        stmt = select(DeletedAccount).where(DeletedAccount.tenant_id == uuid.UUID(account.tenant_id))
        return list((await db.execute(stmt)).scalars())


async def test_the_owner_and_the_store_setup_are_kept_and_no_secrets(account_factory, outbox):
    acct = await account_factory("free")
    tid, sid = uuid.UUID(acct.tenant_id), uuid.UUID(acct.site_id)
    async with SessionLocal() as db:
        db.add(Product(tenant_id=tid, site_id=sid, name="Kept product", slug="kept", price_cents=12_300, currency="BDT"))
        await db.execute(update(User).where(User.tenant_id == tid).values(phone="01700000001", full_name="Kept Owner"))
        await db.execute(
            update(Tenant).where(Tenant.id == tid).values(
                settings={"ai_site_setup": {"inputs": {"what": "panjabi shop"}}, "api_token": "SECRET-VALUE"}
            )
        )
        await db.execute(update(Site).where(Site.id == sid).values(about={"text": "Our story"}))
        await db.commit()
        email = (await db.execute(select(User.email).where(User.tenant_id == tid))).scalar_one()
    await _ready_to_delete([acct], outbox)

    report = await sweep([acct], days=31)

    assert report.deleted == [tid] and not await _exists(acct)
    [record] = await _records(acct)
    assert (record.reason, record.email, record.full_name, record.phone) == ("inactive", email, "Kept Owner", "01700000001")
    assert record.plan == "free" and record.ever_paid is False and record.subdomain
    store = record.snapshot["stores"][0]
    assert store["site"]["about"] == {"text": "Our story"} and store["template_key"]
    assert store["pages"], "the page content they started from"
    assert [(p["name"], p["price_cents"]) for p in store["products"]] == [("Kept product", 12_300)]
    assert record.snapshot["account"]["settings"] == {"ai_site_setup": {"inputs": {"what": "panjabi shop"}}}
    text = str(record.snapshot)
    for secret in ("SECRET-VALUE", "password_hash", "otp"):
        assert secret not in text, secret


async def test_no_record_means_no_deletion(account_factory, outbox, monkeypatch):
    acct = await account_factory("free")
    await _ready_to_delete([acct], outbox)

    async def broken(*a, **kw):
        raise RuntimeError("archive table unavailable")

    monkeypatch.setattr(account_archive, "archive", broken)

    report = await sweep([acct], days=31)

    assert report.deleted == [] and await _exists(acct)


async def test_a_spared_account_leaves_no_record(account_factory, outbox, monkeypatch):
    """The DELETE changes its mind: the record must go with it."""
    acct = await account_factory("free")
    await _ready_to_delete([acct], outbox)
    real = inactivity.delete_inactive_stmt
    # As if the owner came back between the choice and the DELETE.
    monkeypatch.setattr(inactivity, "delete_inactive_stmt", lambda t, seen, limit, now: real(t, seen + DAY, limit, now))

    report = await sweep([acct], days=31)

    assert report.deleted == [] and await _exists(acct)
    assert await _records(acct) == []


async def test_deleting_one_store_keeps_its_details(account_factory):
    acct = await account_factory("business")
    sid = uuid.UUID(acct.site_id)

    res = await acct.client.delete(f"/sites/{sid}", headers=acct.headers)

    assert res.status_code == 204, res.text
    [record] = await _records(acct)
    assert record.reason == "store deleted"
    assert [s["site"]["id"] for s in record.snapshot["stores"]] == [str(sid)]
    assert await _exists(acct)


def test_secret_looking_keys_are_dropped_at_any_depth():
    cleaned = account_archive._clean(
        {"a": 1, "password_hash": "x", "nest": [{"access_token": "y", "api_key": "z", "keep": "k"}], "client_secret": "s"}
    )
    assert cleaned == {"a": 1, "nest": [{"keep": "k"}]}
