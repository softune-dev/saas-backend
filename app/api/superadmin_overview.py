"""Platform operator overview: growth / revenue / AI trends with period-over-
period change, and system health curves. Read-only and cross-tenant, gated by
SuperAdminUser exactly like app/api/superadmin.py (see its docstring for why
rule 1 does not apply here).

Trends are computed from ONE query per metric that covers the current window
and the one before it, split in Python. That is what makes "up 12% on the
previous 30 days" cheap: two windows cost the same number of round trips as
one.
"""

import time
from collections import defaultdict
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.config import settings
from app.db import get_db
from app.models import AiUsageLog, Invoice, Order, SystemHealthSample, Tenant, User
from app.schemas import ORMModel
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin", tags=["superadmin-overview"])
DB = Annotated[AsyncSession, Depends(get_db)]

PAID_PLANS = ("starter", "growth", "business")


# ----------------------------------------------------------------------------
#  Overview
# ----------------------------------------------------------------------------


class KpiOut(ORMModel):
    key: str
    label: str
    value: int
    previous: int
    # None when there is no previous value to compare against.
    delta_pct: float | None
    # "count" or "money_cents": tells the dashboard how to format it.
    format: str


class DayPoint(ORMModel):
    day: str
    signups: int
    orders: int
    gmv_cents: int
    billing_cents: int
    ai_requests: int
    ai_errors: int


class TenantBrief(ORMModel):
    id: uuid.UUID
    name: str
    plan: str
    status: str
    created_at: datetime
    trial_expires_at: datetime | None


class OverviewOut(ORMModel):
    days: int
    kpis: list[KpiOut]
    totals: dict[str, int]
    series: list[DayPoint]
    plans: dict[str, int]
    statuses: dict[str, int]
    recent_tenants: list[TenantBrief]
    expiring_trials: list[TenantBrief]
    attention: list[TenantBrief]


def _day_expr(column):
    return func.to_char(func.date_trunc("day", column), "YYYY-MM-DD")


def _delta(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _brief(t: Tenant) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "plan": t.plan,
        "status": t.status,
        "created_at": t.created_at,
        "trial_expires_at": t.trial_expires_at,
    }


@router.get("/overview", response_model=OverviewOut)
async def overview(
    admin: SuperAdminUser,
    db: DB,
    days: Annotated[int, Query(ge=7, le=180)] = 30,
) -> dict:
    now = datetime.now(UTC)
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_since = since - timedelta(days=days)
    today = now.date()
    window_days = [(since.date() + timedelta(days=i)).isoformat() for i in range(days)]
    boundary = since.date().isoformat()

    async def daily(column, *extra):
        day = _day_expr(column)
        rows = (
            await db.execute(
                select(day, func.count(), *extra)
                .where(column >= prev_since)
                .group_by(day)
            )
        ).all()
        return {r[0]: r[1:] for r in rows}

    signups = await daily(Tenant.created_at)
    # Cancelled orders are not sales; leave them out of the revenue curve.
    orders_ok = {}
    day = _day_expr(Order.created_at)
    for r in (
        await db.execute(
            select(day, func.count(), func.coalesce(func.sum(Order.total_cents), 0))
            .where(Order.created_at >= prev_since, Order.status != "cancelled")
            .group_by(day)
        )
    ).all():
        orders_ok[r[0]] = (r[1], int(r[2]))
    billing = await daily(Invoice.issued_at, func.coalesce(func.sum(Invoice.amount_cents), 0))
    ai_rows = await daily(
        AiUsageLog.created_at,
        func.count().filter(AiUsageLog.status == "error"),
    )

    def split(source: dict, index: int | None = None) -> tuple[int, int]:
        cur = prev = 0
        for d, vals in source.items():
            value = int(vals[0] if index is None else vals[index])
            if d >= boundary:
                cur += value
            else:
                prev += value
        return cur, prev

    s_cur, s_prev = split(signups)
    o_cur, o_prev = split(orders_ok, 0)
    g_cur, g_prev = split(orders_ok, 1)
    b_cur, b_prev = split(billing, 1)
    a_cur, a_prev = split(ai_rows, 0)
    e_cur, e_prev = split(ai_rows, 1)

    def kpi(key, label, cur, prev, fmt="count"):
        return {
            "key": key,
            "label": label,
            "value": cur,
            "previous": prev,
            "delta_pct": _delta(cur, prev),
            "format": fmt,
        }

    kpis = [
        kpi("signups", "New tenants", s_cur, s_prev),
        kpi("orders", "Orders on the platform", o_cur, o_prev),
        kpi("gmv", "Merchant sales", g_cur, g_prev, "money_cents"),
        kpi("billing", "Billed to tenants", b_cur, b_prev, "money_cents"),
        kpi("ai", "AI requests", a_cur, a_prev),
        kpi("ai_errors", "AI failures", e_cur, e_prev),
    ]

    series = [
        {
            "day": d,
            "signups": int(signups.get(d, (0,))[0]),
            "orders": int(orders_ok.get(d, (0, 0))[0]),
            "gmv_cents": int(orders_ok.get(d, (0, 0))[1]),
            "billing_cents": int(billing.get(d, (0, 0))[1]),
            "ai_requests": int(ai_rows.get(d, (0, 0))[0]),
            "ai_errors": int(ai_rows.get(d, (0, 0))[1]),
        }
        for d in window_days
    ]

    total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar_one()
    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    paid = (
        await db.execute(
            select(func.count(Tenant.id)).where(
                Tenant.plan.in_(PAID_PLANS), Tenant.status == "active"
            )
        )
    ).scalar_one()
    active_trials = (
        await db.execute(
            select(func.count(Tenant.id)).where(
                Tenant.plan == "trial", Tenant.trial_expires_at > func.now()
            )
        )
    ).scalar_one()
    in_week = now + timedelta(days=7)
    expiring_rows = (
        await db.execute(
            select(Tenant)
            .where(
                Tenant.plan == "trial",
                Tenant.trial_expires_at > func.now(),
                Tenant.trial_expires_at <= in_week,
            )
            .order_by(Tenant.trial_expires_at)
            .limit(8)
        )
    ).scalars().all()
    attention_rows = (
        await db.execute(
            select(Tenant)
            .where(Tenant.status.in_(("payment_overdue", "suspended")))
            .order_by(Tenant.created_at.desc())
            .limit(8)
        )
    ).scalars().all()
    recent = (
        await db.execute(select(Tenant).order_by(Tenant.created_at.desc()).limit(8))
    ).scalars().all()

    plan_rows = (
        await db.execute(select(Tenant.plan, func.count(Tenant.id)).group_by(Tenant.plan))
    ).all()
    status_rows = (
        await db.execute(select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status))
    ).all()

    return {
        "days": days,
        "kpis": kpis,
        "totals": {
            "tenants": total_tenants,
            "users": total_users,
            "paid_tenants": paid,
            "active_trials": active_trials,
            "trials_expiring_7d": len(expiring_rows),
            "needs_attention": len(attention_rows),
        },
        "series": series,
        "plans": dict(plan_rows),
        "statuses": dict(status_rows),
        "recent_tenants": [_brief(t) for t in recent],
        "expiring_trials": [_brief(t) for t in expiring_rows],
        "attention": [_brief(t) for t in attention_rows],
    }


# ----------------------------------------------------------------------------
#  Health
# ----------------------------------------------------------------------------


class LiveCheck(ORMModel):
    ok: bool
    ms: int | None = None
    detail: str | None = None


class HealthPoint(ORMModel):
    ts: datetime
    requests: int
    errors: int
    p50_ms: int | None
    p95_ms: int | None
    db_ms: int | None
    redis_ms: int | None
    queue_depth: int | None


class RouteStat(ORMModel):
    route: str
    count: int
    avg_ms: int
    errors: int


class HealthSummary(ORMModel):
    requests: int
    errors: int
    error_rate_pct: float
    avg_p50_ms: int | None
    worst_p95_ms: int | None
    availability_pct: float | None
    samples: int


class HealthOut(ORMModel):
    hours: int
    uptime_seconds: int
    live: dict[str, LiveCheck]
    summary: HealthSummary
    series: list[HealthPoint]
    slowest_routes: list[RouteStat]


@router.get("/health", response_model=HealthOut)
async def health(
    admin: SuperAdminUser,
    db: DB,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> dict:
    deps = await metrics.ping_dependencies()
    live = {
        "database": {"ok": deps["db_ms"] is not None, "ms": deps["db_ms"]},
        "redis": {"ok": deps["redis_ms"] is not None, "ms": deps["redis_ms"]},
        "rabbitmq": {
            "ok": bool(deps["rabbit_ok"]),
            "detail": (
                f"{deps['queue_depth']} jobs waiting" if deps["queue_depth"] is not None else None
            ),
        },
        "ai": {
            "ok": bool(settings.gemini_api_key),
            "detail": "Gemini key set" if settings.gemini_api_key else "No Gemini key",
        },
    }

    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(SystemHealthSample)
            .where(SystemHealthSample.sampled_at >= since)
            .order_by(SystemHealthSample.sampled_at)
        )
    ).scalars().all()

    # About 240 points however long the window is, so the charts stay light.
    bucket_seconds = max(60, int(hours * 3600 / 240))
    buckets: dict[int, list[SystemHealthSample]] = defaultdict(list)
    for r in rows:
        buckets[int(r.sampled_at.timestamp() // bucket_seconds)].append(r)

    def avg(values: list[int | None], weights: list[int] | None = None) -> int | None:
        pairs = [
            (v, (weights[i] if weights else 1) or 1)
            for i, v in enumerate(values)
            if v is not None
        ]
        if not pairs:
            return None
        return int(sum(v * w for v, w in pairs) / sum(w for _, w in pairs))

    series = []
    for key in sorted(buckets):
        group = buckets[key]
        weights = [g.requests for g in group]
        p95s = [g.p95_ms for g in group if g.p95_ms is not None]
        depths = [g.queue_depth for g in group if g.queue_depth is not None]
        series.append(
            {
                "ts": datetime.fromtimestamp(key * bucket_seconds, UTC),
                "requests": sum(weights),
                "errors": sum(g.errors_5xx for g in group),
                "p50_ms": avg([g.p50_ms for g in group], weights),
                "p95_ms": max(p95s) if p95s else None,
                "db_ms": avg([g.db_ms for g in group]),
                "redis_ms": avg([g.redis_ms for g in group]),
                "queue_depth": max(depths) if depths else None,
            }
        )

    total_requests = sum(r.requests for r in rows)
    total_errors = sum(r.errors_5xx for r in rows)
    healthy = [r for r in rows if r.db_ms is not None and r.rabbit_ok]
    routes: dict[str, list[float]] = {}
    for r in rows:
        for entry in r.top_routes or []:
            agg = routes.setdefault(entry["route"], [0, 0.0, 0])
            agg[0] += entry["count"]
            agg[1] += entry["avg_ms"] * entry["count"]
            agg[2] += entry["errors"]
    slowest = sorted(
        (
            {
                "route": route,
                "count": int(count),
                "avg_ms": int(total / count) if count else 0,
                "errors": int(errs),
            }
            for route, (count, total, errs) in routes.items()
            if count
        ),
        key=lambda x: x["avg_ms"],
        reverse=True,
    )[:8]

    return {
        "hours": hours,
        "uptime_seconds": int(time.time() - metrics.STARTED_AT),
        "live": live,
        "summary": {
            "requests": total_requests,
            "errors": total_errors,
            "error_rate_pct": round(total_errors / total_requests * 100, 2) if total_requests else 0.0,
            "avg_p50_ms": avg([r.p50_ms for r in rows], [r.requests for r in rows]),
            "worst_p95_ms": max((r.p95_ms for r in rows if r.p95_ms is not None), default=None),
            "availability_pct": round(len(healthy) / len(rows) * 100, 2) if rows else None,
            "samples": len(rows),
        },
        "series": series,
        "slowest_routes": slowest,
    }
