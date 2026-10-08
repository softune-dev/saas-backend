"""In-process request metrics and the once-a-minute health sampler behind the
superadmin dashboard's load / latency / error curves (migrations/073).

HOW IT WORKS: the timing middleware in app/main.py calls observe() for every
response. That only appends to an in-memory bucket, so it costs nothing on the
request path. A background task started in the app lifespan wakes up every
minute, swaps the bucket out, turns it into one system_health_samples row
(count, 5xx, p50/p95/max, slowest routes), adds a ping of Postgres / Redis /
RabbitMQ and the queue depth, and writes it.

RULE 9 APPLIES: metrics must never fail or slow a request. observe() cannot
raise, and every part of the sampler is wrapped so a dead dependency just
becomes a null / false in that minute's row (which is exactly what the curve
should show).

One row is written per API process; the dashboard sums rows that share a
minute, so running more than one worker still gives correct totals.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import delete, text

from app import cache, queue
from app.config import settings
from app.db import SessionLocal, engine
from app.models import SystemHealthSample

log = logging.getLogger(__name__)

SAMPLE_EVERY_SECONDS = 60
RETENTION_DAYS = 30
# A minute holds a few thousand requests at most; the cap only exists so a
# traffic spike can't grow the list without bound before the next flush.
_MAX_DURATIONS = 20000
_TOP_ROUTES = 5

STARTED_AT = time.time()


@dataclass
class _Bucket:
    count: int = 0
    errors: int = 0
    durations: list[float] = field(default_factory=list)
    # route template -> [count, total_ms, errors]
    routes: dict[str, list[float]] = field(default_factory=dict)


_bucket = _Bucket()


def observe(elapsed_ms: float, status_code: int, route: str | None) -> None:
    """Record one finished request. Never raises."""
    try:
        b = _bucket
        b.count += 1
        is_error = status_code >= 500
        if is_error:
            b.errors += 1
        if len(b.durations) < _MAX_DURATIONS:
            b.durations.append(elapsed_ms)
        if route:
            entry = b.routes.setdefault(route, [0, 0.0, 0])
            entry[0] += 1
            entry[1] += elapsed_ms
            if is_error:
                entry[2] += 1
    except Exception:  # noqa: BLE001
        pass


def _percentile(sorted_values: list[float], pct: float) -> int | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return int(sorted_values[index])


async def ping_dependencies() -> dict:
    """Live check of Postgres, Redis and RabbitMQ. Returns ms (or None on
    failure) for each and the job-queue depth. Used by the sampler and by the
    dashboard's "right now" panel, so both show the same thing."""
    out: dict = {"db_ms": None, "redis_ms": None, "rabbit_ok": False, "queue_depth": None}

    started = time.perf_counter()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        out["db_ms"] = int((time.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001
        pass

    started = time.perf_counter()
    try:
        await cache.client().ping()
        out["redis_ms"] = int((time.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001
        pass

    try:
        channel = await queue.connect()
        declared = await channel.declare_queue(settings.queue_name, durable=True, passive=True)
        out["rabbit_ok"] = True
        out["queue_depth"] = declared.declaration_result.message_count
    except Exception:  # noqa: BLE001
        pass
    return out


async def _flush() -> None:
    """Turn the current bucket into one row and start a fresh bucket."""
    global _bucket
    bucket, _bucket = _bucket, _Bucket()

    durations = sorted(bucket.durations)
    top = sorted(bucket.routes.items(), key=lambda kv: kv[1][1], reverse=True)[:_TOP_ROUTES]
    deps = await ping_dependencies()

    async with SessionLocal() as session:
        session.add(
            SystemHealthSample(
                requests=bucket.count,
                errors_5xx=bucket.errors,
                p50_ms=_percentile(durations, 0.5),
                p95_ms=_percentile(durations, 0.95),
                max_ms=int(durations[-1]) if durations else None,
                db_ms=deps["db_ms"],
                redis_ms=deps["redis_ms"],
                rabbit_ok=deps["rabbit_ok"],
                queue_depth=deps["queue_depth"],
                top_routes=[
                    {
                        "route": route,
                        "count": int(count),
                        "avg_ms": int(total / count) if count else 0,
                        "errors": int(errs),
                    }
                    for route, (count, total, errs) in top
                ],
            )
        )
        await session.commit()


async def _prune() -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(SystemHealthSample).where(
                SystemHealthSample.sampled_at < text(f"now() - interval '{RETENTION_DAYS} days'")
            )
        )
        await session.commit()


async def sampler_loop() -> None:
    """Runs for the life of the API process. Started from main.py's lifespan."""
    ticks = 0
    while True:
        await asyncio.sleep(SAMPLE_EVERY_SECONDS)
        try:
            await _flush()
            ticks += 1
            if ticks % 60 == 0:  # about hourly
                await _prune()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("health sampler tick failed", exc_info=True)
