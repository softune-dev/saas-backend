"""Which plan feature each AI tool, undo and error path needs: one small map,
checked in one place, instead of a feature test scattered over every handler.

The dashboard routes already lock the AI itself ("ai") and ChatGPT
("chatgpt") at their entry. This module is what holds AFTER the entry, and it
reads the plan live on every call: a stream that began before a downgrade, a
change queued earlier and an old ChatGPT token must each feel the new plan on
their very next tool call or undo.

Adding an agent tool that touches a locked area? Add it to TOOL_FEATURE (and,
if it stores a new undo shape, to the UNDO_* maps) or it is open to everyone.
"""

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import plan_gate, plans

# Tool name -> the plan feature behind the data it reads or the column it writes.
TOOL_FEATURE: dict[str, str] = {
    "search_customers": "customers",
    "get_customer": "customers",
    "get_fraud_status": "fraud",
    "update_fraud_rules": "fraud",
    "update_blocklist": "fraud",
    "review_suspicious_order": "fraud",
    "update_seo": "seo",
    "request_custom_domain": "domain",
}
# get_store_admin_info serves several areas through one tool, by `section`.
ADMIN_SECTION_FEATURE: dict[str, str] = {"domain": "domain", "fraud": "fraud"}

# What an AiAgentChange.undo spec restores. "site_json" is keyed by the column
# it wrote, the other kinds by their type.
UNDO_COLUMN_FEATURE: dict[str, str] = {"seo": "seo", "fraud_rules": "fraud"}
UNDO_KIND_FEATURE: dict[str, str] = {
    "domain_request": "domain",
    "blocklist": "fraud",
    "fraud_review": "fraud",
}


def tool_feature(name: str, args: dict | None = None) -> str | None:
    if name == "get_store_admin_info":
        return ADMIN_SECTION_FEATURE.get(str((args or {}).get("section")))
    return TOOL_FEATURE.get(name)


def undo_feature(spec: dict) -> str | None:
    kind = spec.get("type")
    if kind == "site_json":
        return UNDO_COLUMN_FEATURE.get(str(spec.get("column")))
    return UNDO_KIND_FEATURE.get(str(kind))


async def ensure_tool(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, args: dict | None = None, *, need_ai: bool
) -> None:
    """403 plan_locked when the tenant's CURRENT plan lacks the tool's feature
    (and, for agent tools, the AI itself). Skips the plan lookup entirely for
    a read tool that needs nothing."""
    feature = tool_feature(name, args)
    if feature is None and not need_ai:
        return
    plan = await plan_gate.tenant_plan(db, tenant_id)
    if need_ai:
        plans.ensure_feature(plan, "ai")
    if feature is not None:
        plans.ensure_feature(plan, feature)


async def ensure_undo(db: AsyncSession, tenant_id: uuid.UUID, spec: dict) -> None:
    """Undo is an AI feature, and putting back a locked column is refused too:
    a change recorded before a downgrade must not write SEO or fraud rules
    the plan no longer allows."""
    plan = await plan_gate.tenant_plan(db, tenant_id)
    plans.ensure_feature(plan, "ai")
    feature = undo_feature(spec)
    if feature is not None:
        plans.ensure_feature(plan, feature)


def detail_message(detail: Any) -> str:
    """An HTTPException detail as text for a person or a model. plan_locked
    carries a dict; str() on it would show a Python dict."""
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or "That isn't available right now.")
    return str(detail)


def locked_code(detail: Any) -> str | None:
    return detail.get("code") if isinstance(detail, dict) else None


async def tenant_has_feature(db: AsyncSession, tenant_id: uuid.UUID, feature: str) -> bool:
    """The yes/no form of plan_gate.ensure, for callers that answer in their
    own words (ChatGPT's plain-sentence errors). A missing tenant is no."""
    try:
        return plans.has_feature(await plan_gate.tenant_plan(db, tenant_id), feature)
    except HTTPException:
        return False


# Sign-up chat: a Free store has no AI, but the setup wizard's products step
# asks /ai/chat for product text. That one door is text-only (no tools), capped
# per day (ai._check_onboarding_chat) and shut once onboarding is done or the
# store is older than this, so a downgraded or abandoned store can't keep it.
ONBOARDING_CHAT_DAYS = 7


async def onboarding_open(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True while the tenant's first site has not finished onboarding. Read
    from the site row, never from anything the client sends."""
    from sqlalchemy import select

    from app.models import Site

    row = (
        await db.execute(
            select(Site.onboarding_completed_at)
            .where(Site.tenant_id == tenant_id)
            .order_by(Site.created_at)
            .limit(1)
        )
    ).first()
    return row is not None and row[0] is None


async def onboarding_chat_open(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import Tenant

    if not await onboarding_open(db, tenant_id):
        return False
    created = (await db.execute(select(Tenant.created_at).where(Tenant.id == tenant_id))).scalar_one_or_none()
    return created is not None and datetime.now(UTC) - created <= timedelta(days=ONBOARDING_CHAT_DAYS)
