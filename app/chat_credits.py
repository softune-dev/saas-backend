"""Purchasable chat-credit ledger — extends the free daily text-chat
allowance (app/ai.py's PLAN_AI_DAILY_CAP, a Redis-only counter, unaffected
by this) rather than replacing it. Once a tenant burns through today's free
count, _check_ai_access spends one of these instead of a hard 429.

PRICING: a Flash-Lite text call costs a small fraction of a cent — nowhere
near an image generation's real per-call cost (see app/ai_images.py's own
docstring for that math). These packs are priced to feel like a cheap
"never get blocked" convenience, not a serious line item, while still being
real, paid, and profitable. 1 credit = 1 request past the free daily cap.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatCreditTransaction, Tenant

# Same manual-bKash-claim pattern as ai_images.py's CREDIT_PACKS — mirrored
# on the dashboard side in components/ai-image/credit-packs-data.ts by
# hand, same tradeoff that file's own docstring already accepts.
CHAT_CREDIT_PACKS: dict[str, dict] = {
    "starter": {"name": "Starter", "credits": 100, "price_taka": 100},
    "popular": {"name": "Popular", "credits": 300, "price_taka": 250},
    "pro": {"name": "Pro", "credits": 1000, "price_taka": 700},
}


async def get_balance(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    return tenant.chat_credits


async def spend_one_credit(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Deducts exactly 1 credit and returns the new balance. Raises 402 if
    the tenant has none left — same as ai_images.charge_credits, this is
    the ONLY thing that should ever stop a request once the free daily cap
    is gone; app/ai.py's _check_ai_access calls this itself, so this module
    never needs to know why it's being spent.
    """
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    if tenant.chat_credits < 1:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Today's free AI requests are used up — buy chat credits to keep going.",
        )
    tenant.chat_credits -= 1
    db.add(
        ChatCreditTransaction(
            tenant_id=tenant_id, delta=-1, reason="spend", balance_after=tenant.chat_credits,
        )
    )
    await db.commit()
    return tenant.chat_credits


async def grant_credits(
    db: AsyncSession, tenant_id: uuid.UUID, credits: int, reason: str, reference: str | None = None
) -> int:
    """Shared by a confirmed pack purchase and a superadmin manual grant —
    identical to app/ai_images.py's grant_credits, just a different ledger
    table/column."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    tenant.chat_credits += credits
    db.add(
        ChatCreditTransaction(
            tenant_id=tenant_id, delta=credits, reason=reason,
            balance_after=tenant.chat_credits, reference=reference,
        )
    )
    await db.commit()
    return tenant.chat_credits
