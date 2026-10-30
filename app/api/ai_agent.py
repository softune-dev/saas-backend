"""AI agent endpoints: the streaming chat (app/ai_agent.py) and Undo for the
changes it makes (app/ai_agent_undo.py).

Tenant-wide, not scoped to a site_id in the URL — like /ai/chat, the agent is
opened from the global header; every tool resolves the tenant's own site from
the verified JWT.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai, ai_agent, ai_agent_undo
from app.api.ai import _tenant_plan
from app.db import get_db
from app.security import CurrentUser

router = APIRouter(prefix="/ai", tags=["ai-agent"])
DB = Annotated[AsyncSession, Depends(get_db)]


class AgentTurn(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class AgentChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[AgentTurn] = Field(default_factory=list, max_length=40)
    # A dashboard context tag ("focus on my orders") — sent apart from the
    # message so chat history and logs keep only what the merchant typed.
    context_hint: str | None = Field(default=None, max_length=500)


@router.post("/agent/chat")
async def agent_chat(payload: AgentChatIn, user: CurrentUser, db: DB) -> StreamingResponse:
    """Server-Sent Events. The credit check runs BEFORE the stream opens, so
    "daily limit reached" comes back as a normal 429 the dashboard already
    handles, not as an event halfway through a reply."""
    ai._ensure_configured()
    plan = await _tenant_plan(db, user.tenant_id)
    await ai._check_chat_access(str(user.tenant_id), plan, db)
    stream = ai_agent.run(
        message=payload.message,
        history=[t.model_dump() for t in payload.history],
        context_hint=payload.context_hint,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        plan=plan,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        # no-transform/X-Accel-Buffering: a proxy that buffers the response
        # would turn the live stream back into one late blob.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/changes/{change_id}/undo")
async def undo_change(change_id: uuid.UUID, user: CurrentUser, db: DB) -> dict:
    return await ai_agent_undo.undo(db, user.tenant_id, change_id)
