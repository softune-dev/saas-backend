"""When did this merchant last do something real? (tenants.last_active_at)

A later sweep removes free stores that have been abandoned, so this column has
to be trustworthy: it must move when a person uses the dashboard (or the
ChatGPT connector, or receives an order) and must NOT move because a dashboard
tab was left open and is polling by itself.

It runs inside every authenticated request, so it is built to be unnoticeable:

  - A per-process dict allows one write per tenant per hour. The common case is
    one dict lookup, no I/O, and it needs no Redis.
  - The UPDATE itself also says "only if older than an hour", so several API
    processes (or a restart that empties the dict) cannot write repeatedly.
  - A failure is logged and NOT retried until the next hour. A database that is
    struggling must not be asked again by every following request.
  - It uses its own short session so a failure can never poison the request's
    transaction. At most MAX_IN_FLIGHT run at once and each is bounded by
    TIMEOUT_SECONDS, so it can never take over the small connection pool
    (5 + 10, app/db.py). Call it while holding no connection of your own (before
    the request's first query, or after its commit): a caller that held one
    while waiting here for a second is how a pool deadlocks.
"""

import asyncio
import logging
import re
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.sql.dml import Update

from app.db import SessionLocal
from app.models import Tenant

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=1)
# Generous on purpose: the pooler has been slow at times (app/db.py), and a
# limit that a merely slow database always hits would make every merchant look
# inactive. The price is at worst one slower request per merchant per hour.
TIMEOUT_SECONDS = 5.0
MAX_IN_FLIGHT = 3
# About a hundred bytes an entry, so this is roughly a megabyte at worst.
MAX_TRACKED = 10_000

# tenant -> time.monotonic() of the last attempt. Oldest first (an entry is
# re-inserted when it is refreshed), which makes trimming a pop from the front.
_attempted: dict[uuid.UUID, float] = {}
_in_flight = 0

# GET requests the dashboard repeats on a timer (every SWR refreshInterval in
# dashboard/lib/api; superadmin's own polls carry superadmin tokens, which
# never count). Adding a new poll to the dashboard? Add it here, or a forgotten
# open tab keeps an abandoned store alive forever. Anchored at the end, not the
# start, so a root_path or /api prefix in front changes nothing.
_POLLED = re.compile(
    r"(?:^|/)(?:"
    r"sites/[^/]+/notifications"  # header bell, every 5s
    r"|sites/[^/]+/pay/devices"  # Softune Pay, every 3-15s
    r"|sites/[^/]+/pay/payments"  # Softune Pay, every 15s
    r"|ai/usage"  # header AI credits, every 60s
    r")/*$"
)


def is_automatic(path: str) -> bool:
    return _POLLED.search(path) is not None


def due(tenant_id: uuid.UUID) -> bool:
    """Would touch() reach for the database now? Lets a caller that holds a
    connection give it back first, without paying for that on every call."""
    last = _attempted.get(tenant_id)
    return last is None or time.monotonic() - last >= WINDOW.total_seconds()


def touch_stmt(tenant_id: uuid.UUID) -> Update:
    """The write itself, for code that would rather run it in a transaction of
    its own (a new order) than call touch().

    SKIP LOCKED: something else may be holding this tenant's row (a payment
    being applied, credits being spent). Waiting for it would stall the caller
    over a timestamp, so it is skipped and the next window records it. The
    locked row is re-checked by Postgres, so two processes racing here produce
    one write.
    """
    stale = (
        select(Tenant.id)
        .where(Tenant.id == tenant_id, Tenant.last_active_at < func.now() - WINDOW)
        # NO KEY: the weakest lock an UPDATE of a plain column takes; it does
        # not block the foreign key checks of concurrent inserts (orders...).
        .with_for_update(skip_locked=True, key_share=True)
    )
    return (
        update(Tenant)
        .where(Tenant.id.in_(stale))
        .values(last_active_at=func.now())
        .execution_options(synchronize_session=False)
    )


def _remember(tenant_id: uuid.UUID) -> None:
    _attempted.pop(tenant_id, None)
    _attempted[tenant_id] = time.monotonic()
    while len(_attempted) > MAX_TRACKED:
        del _attempted[next(iter(_attempted))]


async def _write(tenant_id: uuid.UUID) -> None:
    # An ordinary short transaction, not autocommit: on the port-6543
    # transaction pooler a statement outside a transaction can have its prepare
    # and execute land on different backends (app/db.py). If we are cancelled
    # mid-way, closing the session rolls the transaction back.
    async with SessionLocal() as db, db.begin():
        await db.execute(touch_stmt(tenant_id))


async def touch(tenant_id: uuid.UUID, *, path: str = "") -> None:
    """Best effort "this merchant is active now". Never raises. `path` is the
    request path of a GET, so automatic polling can be told from real use."""
    global _in_flight
    if not due(tenant_id) or is_automatic(path) or _in_flight >= MAX_IN_FLIGHT:
        # Too many at once is not recorded as an attempt: the tenant's next
        # request simply tries again.
        return
    # Before the await, so the burst of requests one page load makes sends one
    # write, and kept on failure, so a broken database is not hammered.
    _remember(tenant_id)
    _in_flight += 1
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            await _write(tenant_id)
    except Exception as exc:  # noqa: BLE001 - a timestamp must never fail a request
        log.warning("couldn't record activity for tenant %s: %r", tenant_id, exc)
    finally:
        _in_flight -= 1
