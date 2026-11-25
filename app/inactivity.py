"""Free is free as long as you're active. This deletes the Free accounts that are
not, after three warning emails. Rules approved by the owner:
docs/superpowers/plans/2026-11-26-free-plan-5-inactivity.md.

Deletion cannot be undone, so every doubt resolves to "keep it": anything that
is not plainly an abandoned Free account is left alone, the DELETE itself checks
everything again, and a run that wants to delete many accounts deletes none.

INACTIVITY_DELETION (app/config.py) is off, report or on. It exists because a
developer's worker talks to the live database.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Delete, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import account_archive, contacts, mailer, plans
from app.config import settings
from app.db import SessionLocal
from app.models import PaymentClaim, Site, Tenant, TenantInactivity, User

log = logging.getLogger(__name__)

# Full inactive period -> the days on which the three warnings fall due.
WARNING_DAYS: dict[int, tuple[int, int, int]] = {
    plans.FREE_INACTIVE_DAYS: (15, 23, 29),
    plans.FORMER_PAID_INACTIVE_DAYS: (60, 75, 85),
}
# If the worker was down, warnings move later. They are never squeezed together.
MIN_GAP = timedelta(days=5)
FINAL_NOTICE = timedelta(hours=24)
# More than this at once looks like a bug (a broken activity clock, a bad
# migration), not like twenty-one people leaving on the same day.
MAX_DELETIONS = 20
MODES = ("off", "report", "on")

_alerted_on = None  # the day the owner was last told a run was stopped


@dataclass(frozen=True)
class Warned:
    stage: int
    at: datetime
    for_activity: datetime


@dataclass
class Report:
    warned: list[uuid.UUID] = field(default_factory=list)
    deleted: list[uuid.UUID] = field(default_factory=list)
    # Filled in report mode: (tenant, "warn 1" ... "delete").
    would: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    capped: bool = False


def inactive_limit(paid_before: bool) -> int:
    return plans.FORMER_PAID_INACTIVE_DAYS if paid_before else plans.FREE_INACTIVE_DAYS


def next_step(now: datetime, last_active: datetime, paid_before: bool, warned: Warned | None) -> str | None:
    """What is due for one eligible account: "warn 1", "warn 2", "warn 3",
    "delete" or nothing. One step at a time, whatever the delay."""
    limit = inactive_limit(paid_before)
    first, second, third = WARNING_DAYS[limit]
    idle = now - last_active
    # Warnings counted before the latest activity are void.
    stage = warned.stage if warned and warned.for_activity == last_active else 0

    if stage == 0:
        return "warn 1" if idle >= timedelta(days=first) else None
    since = now - warned.at
    if stage == 1:
        return "warn 2" if idle >= timedelta(days=second) and since >= MIN_GAP else None
    if stage == 2:
        return "warn 3" if idle >= timedelta(days=third) and since >= MIN_GAP else None
    return "delete" if idle >= timedelta(days=limit) and since >= FINAL_NOTICE else None


def _eligible() -> tuple:
    """Only an ordinary, active Free account with nothing waiting on us."""
    return (
        Tenant.plan == "free",
        Tenant.status == "active",
        ~exists().where(User.tenant_id == Tenant.id, User.is_superadmin.is_(True)),
        # Money sent and not yet confirmed: a plan, or a credit pack.
        ~exists().where(PaymentClaim.tenant_id == Tenant.id, PaymentClaim.status == "pending"),
    )


def delete_inactive_stmt(tenant_id: uuid.UUID, seen_activity: datetime, limit_days: int, now: datetime) -> Delete:
    """Re-checks everything inside the DELETE. Between choosing an account and
    deleting it, its owner may log in, pay, or send a payment claim."""
    return (
        delete(Tenant)
        .where(
            Tenant.id == tenant_id,
            *_eligible(),
            Tenant.last_active_at == seen_activity,
            Tenant.last_active_at <= now - timedelta(days=limit_days),
            exists().where(
                TenantInactivity.tenant_id == Tenant.id,
                TenantInactivity.stage == 3,
                TenantInactivity.warned_for == Tenant.last_active_at,
                TenantInactivity.warned_at <= now - FINAL_NOTICE,
            ),
        )
        .returning(Tenant.id)
        .execution_options(synchronize_session=False)
    )


async def run(
    *, mode: str | None = None, tenant_ids: list[uuid.UUID] | None = None, now: datetime | None = None
) -> Report:
    """One pass. `tenant_ids` limits it to those accounts (an empty list means
    none); tests always pass it, so they cannot touch a real store."""
    mode = (mode or settings.inactivity_deletion or "off").strip().lower()
    report = Report()
    if mode not in MODES:
        log.error("INACTIVITY_DELETION=%r is not one of %s; doing nothing", mode, MODES)
        return report
    if mode == "off":
        return report
    now = now or datetime.now(UTC)
    earliest = min(days[0] for days in WARNING_DAYS.values())

    async with SessionLocal() as db:
        stmt = select(Tenant.id, Tenant.last_active_at).where(*_eligible(), Tenant.last_active_at <= now - timedelta(days=earliest))
        if tenant_ids is not None:
            stmt = stmt.where(Tenant.id.in_(tenant_ids))
        accounts = (await db.execute(stmt)).all()
        if not accounts:
            return report
        ids = [a.id for a in accounts]
        paid = await contacts.paid_before(db, ids)
        rows = (await db.execute(select(TenantInactivity).where(TenantInactivity.tenant_id.in_(ids)))).scalars()
        states = {r.tenant_id: Warned(r.stage, r.warned_at, r.warned_for) for r in rows}

    steps = []
    for account in accounts:
        step = next_step(now, account.last_active_at, account.id in paid, states.get(account.id))
        if step:
            steps.append((account.id, account.last_active_at, account.id in paid, step))

    if mode == "report":
        for tenant_id, last_active, paid_before, step in steps:
            report.would.append((tenant_id, step))
            log.info(
                "inactivity (report only): tenant %s, idle %d days, limit %d: would %s",
                tenant_id, (now - last_active).days, inactive_limit(paid_before), step,
            )
        return report

    for tenant_id, last_active, paid_before, step in steps:
        if step != "delete" and await _warn(tenant_id, last_active, paid_before, int(step.split()[1]), now):
            report.warned.append(tenant_id)

    doomed = [(t, a, inactive_limit(p)) for t, a, p, step in steps if step == "delete"]
    if len(doomed) > MAX_DELETIONS:
        report.capped = True
        await _alert_capped(len(doomed), now)
    elif doomed:
        report.deleted = await _delete(doomed, now)
    return report


async def _warn(tenant_id: uuid.UUID, seen_activity: datetime, paid_before: bool, number: int, now: datetime) -> bool:
    """Sends one warning and records it only if the email really went out. An
    owner we cannot email is therefore never deleted."""
    async with SessionLocal() as db:
        # Still the same abandoned account? Earlier emails in this run took time.
        still = await db.execute(
            select(Tenant.id).where(Tenant.id == tenant_id, *_eligible(), Tenant.last_active_at == seen_activity)
        )
        if still.scalar_one_or_none() is None:
            return False
        owner = (
            await db.execute(select(User).where(User.tenant_id == tenant_id, User.role == "owner"))
        ).scalars().first()
        if owner is None:
            return False
        email, name = owner.email, owner.full_name

    limit = inactive_limit(paid_before)
    idle = (now - seen_activity).days
    # The date we can promise: never sooner than the rules allow from today.
    remaining = WARNING_DAYS[limit]
    wait = FINAL_NOTICE
    for earlier, later in zip(remaining[number - 1 :], remaining[number:]):
        wait += max(MIN_GAP, timedelta(days=later - earlier))
    delete_on = max(seen_activity + timedelta(days=limit), now + wait)

    subject, html_body, text_body = mailer.free_inactivity_warning_email(
        name, number=number, idle_days=idle, delete_on=delete_on.strftime("%d %b %Y")
    )
    if not await mailer.send_email(email, subject, html_body, text_body):
        log.warning("inactivity warning %d for tenant %s was not sent; will retry", number, tenant_id)
        return False

    async with SessionLocal() as db:
        values = {"stage": number, "warned_at": now, "warned_for": seen_activity}
        await db.execute(
            pg_insert(TenantInactivity)
            .values(tenant_id=tenant_id, **values)
            .on_conflict_do_update(index_elements=[TenantInactivity.tenant_id], set_=values)
        )
        await db.commit()
    log.info("inactivity: sent warning %d to tenant %s (idle %d days)", number, tenant_id, idle)
    return True


async def _delete(doomed: list[tuple[uuid.UUID, datetime, int]], now: datetime) -> list[uuid.UUID]:
    ids = [t for t, _, _ in doomed]
    async with SessionLocal() as db:
        # Read hostnames before the cascade: afterwards there is nothing to read.
        sites = (await db.execute(select(Site).where(Site.tenant_id.in_(ids)))).scalars().all()
        # The owners are saved to Contacts FIRST. If that fails, nobody is
        # deleted this round.
        try:
            await contacts.upsert_tenants(db, ids, deleted=True)
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.exception("inactivity: couldn't save contacts; nothing deleted this run")
            return []
    deleted: list[uuid.UUID] = []
    for tenant_id, seen_activity, limit in doomed:
        # One transaction each: the record and the deletion happen together or
        # not at all.
        async with SessionLocal() as db:
            try:
                await account_archive.archive(db, tenant_id, "inactive")
                gone = (await db.execute(delete_inactive_stmt(tenant_id, seen_activity, limit, now))).scalar_one_or_none()
                if gone is None:
                    await db.rollback()
                    continue
                await db.commit()
                deleted.append(gone)
            except Exception:  # noqa: BLE001 - this one is kept; the others go on
                await db.rollback()
                log.exception("inactivity: couldn't archive and delete tenant %s; kept", tenant_id)

    if deleted:
        log.info("inactivity: deleted %d abandoned free tenant(s): %s", len(deleted), deleted)
    for site in sites:
        # A spared tenant is live: its media and domains stay.
        if site.tenant_id in deleted:
            await _clean_up(site)
    return deleted


async def _clean_up(site: Site) -> None:
    """What the database cascade cannot reach: cache, domains and media."""
    from fastapi.concurrency import run_in_threadpool

    from app import cache, media, vercel

    await cache.invalidate_site(site.subdomain, site.custom_domain)
    project_id = site.template.vercel_project_id if site.template else None
    if project_id and site.template.framework == "nextjs":
        await vercel.remove_domain_from_project(f"{site.subdomain}.{settings.site_base_domain}", project_id)
        if site.custom_domain:
            await vercel.remove_domain_from_project(site.custom_domain, project_id)
    await run_in_threadpool(media.delete_site_folder, site.subdomain)


async def _alert_capped(count: int, now: datetime) -> None:
    """Tells the owner a run was stopped, at most once a day."""
    global _alerted_on
    log.error("inactivity: %d accounts due for deletion (limit %d); deleted NONE", count, MAX_DELETIONS)
    if _alerted_on == now.date():
        return
    subject, html_body, text_body = mailer.inactivity_run_stopped_email(count, MAX_DELETIONS)
    if await mailer.send_email(settings.billing_notify_email, subject, html_body, text_body):
        _alerted_on = now.date()
