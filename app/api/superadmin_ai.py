"""Platform operator view of every AI call: chat, text generation, theme
suggestions, image generate/edit. Read-only; the rows are written by
app/ai_log.py as the calls happen (see migrations/072 for the table).

Same cross-tenant exception as app/api/superadmin.py: every route is gated by
SuperAdminUser, so rule 1 (tenant isolation through crud.py) does not apply.
The log holds merchants' prompts and the model's replies, which is the point
of this page and also why it is operator-only.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Integer, Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.api.superadmin import site_logo_url
from app.models import AiUsageLog, Site, Tenant, User
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin/ai", tags=["superadmin-ai"])
DB = Annotated[AsyncSession, Depends(get_db)]

AiKind = Literal["chat", "generate_text", "theme_suggest", "image_generate", "image_edit"]
AiStatus = Literal["ok", "error"]
IMAGE_KINDS = ("image_generate", "image_edit")


class AiUsageItemOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    tenant_id: uuid.UUID
    tenant_name: str
    user_id: uuid.UUID | None
    user_email: str | None
    tenant_logo_url: str | None = None
    user_avatar_url: str | None = None
    kind: str
    status: str
    model: str | None
    # The line an operator scans: the merchant's message / prompt.
    preview: str
    total_tokens: int | None
    latency_ms: int | None
    credits_charged: int
    free_onboarding: bool
    has_thumbnail: bool
    error: str | None


class AiUsageDetailOut(AiUsageItemOut):
    input: dict[str, Any]
    output: dict[str, Any]
    meta: dict[str, Any]
    thumbnail: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    gemini_calls: int


class AiKindStat(ORMModel):
    kind: str
    count: int
    errors: int
    total_tokens: int
    avg_latency_ms: int


class AiDayStat(ORMModel):
    day: str
    count: int
    errors: int
    chats: int
    images: int
    total_tokens: int


class AiTenantStat(ORMModel):
    tenant_id: uuid.UUID
    tenant_name: str
    count: int
    images: int
    chats: int
    total_tokens: int
    credits_charged: int


class AiSummaryOut(ORMModel):
    days: int
    total_requests: int
    errors: int
    active_tenants: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    avg_latency_ms: int
    images_generated: int
    free_images_used: int
    image_credits_charged: int
    chats: int
    by_kind: list[AiKindStat]
    by_day: list[AiDayStat]
    top_tenants: list[AiTenantStat]


def _preview(kind: str, input_: dict, output: dict) -> str:
    """One scannable line per row. Falls back through the keys each kind
    stores so an older or partial row still shows something."""
    if kind == "chat":
        text = input_.get("message")
    elif kind in IMAGE_KINDS:
        text = (
            input_.get("merchant_prompt")
            or input_.get("subject")
            or input_.get("prompt_sent")
        )
    elif kind == "generate_text":
        ctx = input_.get("context") or {}
        text = f"{input_.get('kind', '')}: " + str(
            ctx.get("name") or ctx.get("product_name") or ctx.get("site_name") or ""
        )
    elif kind == "theme_suggest":
        text = input_.get("prompt")
    else:
        text = None
    text = str(text or "").strip().replace("\n", " ")
    return text[:200]


def _item(
    row: AiUsageLog,
    tenant_name: str,
    user_email: str | None,
    avatar_url: str | None = None,
    logo_url: str | None = None,
) -> dict:
    return {
        "tenant_logo_url": logo_url,
        "user_avatar_url": avatar_url,
        "id": row.id,
        "created_at": row.created_at,
        "tenant_id": row.tenant_id,
        "tenant_name": tenant_name,
        "user_id": row.user_id,
        "user_email": user_email,
        "kind": row.kind,
        "status": row.status,
        "model": row.model,
        "preview": _preview(row.kind, row.input or {}, row.output or {}),
        "total_tokens": row.total_tokens,
        "latency_ms": row.latency_ms,
        "credits_charged": int((row.output or {}).get("credits_charged") or 0),
        "free_onboarding": bool((row.meta or {}).get("free_onboarding")),
        "has_thumbnail": bool(row.thumbnail),
        "error": row.error,
    }


@router.get("/usage", response_model=Page[AiUsageItemOut])
async def list_usage(
    admin: SuperAdminUser,
    db: DB,
    kind: AiKind | None = None,
    status_filter: Annotated[AiStatus | None, Query(alias="status")] = None,
    tenant_id: uuid.UUID | None = None,
    q: str | None = None,
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = []
    if kind:
        filters.append(AiUsageLog.kind == kind)
    if status_filter:
        filters.append(AiUsageLog.status == status_filter)
    if tenant_id:
        filters.append(AiUsageLog.tenant_id == tenant_id)
    if days:
        filters.append(AiUsageLog.created_at >= datetime.now(UTC) - timedelta(days=days))
    if q:
        like = f"%{q}%"
        # Searching the JSON as text is a seq scan over the filtered rows;
        # fine for an operator tool at this volume, and the other filters
        # (kind/tenant/days) narrow the set first.
        filters.append(
            or_(
                cast(AiUsageLog.input, Text).ilike(like),
                cast(AiUsageLog.output, Text).ilike(like),
                AiUsageLog.error.ilike(like),
                Tenant.name.ilike(like),
            )
        )

    total = (
        await db.execute(
            select(func.count())
            .select_from(AiUsageLog)
            .join(Tenant, Tenant.id == AiUsageLog.tenant_id)
            .where(*filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(AiUsageLog, Tenant.name, User.email, User.avatar_url)
            .join(Tenant, Tenant.id == AiUsageLog.tenant_id)
            .outerjoin(User, User.id == AiUsageLog.user_id)
            .where(*filters)
            .order_by(AiUsageLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    logos: dict[uuid.UUID, str | None] = {}
    tenant_ids = list({r.tenant_id for r, *_ in rows})
    if tenant_ids:
        for tid, theme in (
            await db.execute(
                select(Site.tenant_id, Site.theme).where(Site.tenant_id.in_(tenant_ids)).order_by(Site.created_at)
            )
        ).all():
            if logos.get(tid) is None:
                logos[tid] = site_logo_url(theme)
    return {
        "items": [
            _item(r, tenant_name, email, avatar, logos.get(r.tenant_id))
            for r, tenant_name, email, avatar in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/usage/{log_id}", response_model=AiUsageDetailOut)
async def get_usage(log_id: uuid.UUID, admin: SuperAdminUser, db: DB) -> dict:
    row = (
        await db.execute(
            select(AiUsageLog, Tenant.name, User.email, User.avatar_url)
            .join(Tenant, Tenant.id == AiUsageLog.tenant_id)
            .outerjoin(User, User.id == AiUsageLog.user_id)
            .where(AiUsageLog.id == log_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    log, tenant_name, email, avatar = row
    return {
        **_item(log, tenant_name, email, avatar),
        "input": log.input or {},
        "output": log.output or {},
        "meta": log.meta or {},
        "thumbnail": log.thumbnail,
        "prompt_tokens": log.prompt_tokens,
        "completion_tokens": log.completion_tokens,
        "gemini_calls": log.gemini_calls,
    }


@router.get("/summary", response_model=AiSummaryOut)
async def summary(
    admin: SuperAdminUser,
    db: DB,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    in_window = AiUsageLog.created_at >= since

    is_image = AiUsageLog.kind.in_(IMAGE_KINDS)
    is_error = AiUsageLog.status == "error"
    ok_image = is_image & (AiUsageLog.status == "ok")
    credits = func.coalesce(
        func.sum(cast(AiUsageLog.output["credits_charged"].astext, Integer)), 0
    )
    free_flag = AiUsageLog.meta["free_onboarding"].astext == "true"

    totals = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(is_error),
                func.count(func.distinct(AiUsageLog.tenant_id)),
                func.coalesce(func.sum(AiUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(AiUsageLog.completion_tokens), 0),
                func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
                func.coalesce(func.avg(AiUsageLog.latency_ms), 0),
                func.count().filter(ok_image),
                func.count().filter(ok_image & free_flag),
                credits,
                func.count().filter(AiUsageLog.kind == "chat"),
            ).where(in_window)
        )
    ).one()

    by_kind = (
        await db.execute(
            select(
                AiUsageLog.kind,
                func.count(),
                func.count().filter(is_error),
                func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
                func.coalesce(func.avg(AiUsageLog.latency_ms), 0),
            )
            .where(in_window)
            .group_by(AiUsageLog.kind)
            .order_by(func.count().desc())
        )
    ).all()

    day = func.to_char(func.date_trunc("day", AiUsageLog.created_at), "YYYY-MM-DD")
    by_day = (
        await db.execute(
            select(
                day,
                func.count(),
                func.count().filter(is_error),
                func.count().filter(AiUsageLog.kind == "chat"),
                func.count().filter(is_image),
                func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
            )
            .where(in_window)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    top = (
        await db.execute(
            select(
                AiUsageLog.tenant_id,
                Tenant.name,
                func.count(),
                func.count().filter(is_image),
                func.count().filter(AiUsageLog.kind == "chat"),
                func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
                credits,
            )
            .join(Tenant, Tenant.id == AiUsageLog.tenant_id)
            .where(in_window)
            .group_by(AiUsageLog.tenant_id, Tenant.name)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    return {
        "days": days,
        "total_requests": totals[0],
        "errors": totals[1],
        "active_tenants": totals[2],
        "prompt_tokens": int(totals[3]),
        "completion_tokens": int(totals[4]),
        "total_tokens": int(totals[5]),
        "avg_latency_ms": int(totals[6]),
        "images_generated": totals[7],
        "free_images_used": totals[8],
        "image_credits_charged": int(totals[9]),
        "chats": totals[10],
        "by_kind": [
            {"kind": k, "count": c, "errors": e, "total_tokens": int(t), "avg_latency_ms": int(a)}
            for k, c, e, t, a in by_kind
        ],
        "by_day": [
            {"day": d, "count": c, "errors": e, "chats": ch, "images": im, "total_tokens": int(t)}
            for d, c, e, ch, im, t in by_day
        ],
        "top_tenants": [
            {
                "tenant_id": tid,
                "tenant_name": name,
                "count": c,
                "images": im,
                "chats": ch,
                "total_tokens": int(t),
                "credits_charged": int(cr),
            }
            for tid, name, c, im, ch, t, cr in top
        ],
    }
