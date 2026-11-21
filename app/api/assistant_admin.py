"""Merchant side of the store assistant: the list of questions shoppers asked
that it could not answer, and turning one into a written answer.

Settings themselves (name, avatar, written answers) are written through
PATCH /sites/{id} like every other Site Settings page; this module only owns
the unanswered-questions table (migrations/085).
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud
from app.assistant.schemas import MAX_QA, QA, settings_from_row
from app.db import get_db
from app.models import AssistantQuestion, Site
from app.schemas import AssistantQuestionAnswer, AssistantQuestionOut, AssistantQuestionPatch, Page
from app.security import CurrentUser

router = APIRouter(tags=["assistant"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


async def _owned_question(db: AsyncSession, user: CurrentUser, site: Site, question_id: uuid.UUID) -> AssistantQuestion:
    q = await crud.get_scoped(db, AssistantQuestion, user.tenant_id, question_id)
    # Same tenant but a different site is still "not found" for this URL.
    if q.site_id != site.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return q


@router.get("/sites/{site_id}/assistant/questions", response_model=Page[AssistantQuestionOut])
async def list_questions(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    state: Literal["open", "answered", "dismissed"] = "open",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)
    rows, total = await crud.list_scoped(
        db, AssistantQuestion, user.tenant_id,
        filters=[AssistantQuestion.site_id == site_id, AssistantQuestion.status == state],
        # Most-asked first: the answer that helps the most shoppers comes first.
        order_by=[AssistantQuestion.times_asked.desc(), AssistantQuestion.last_asked_at.desc()],
        limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post("/sites/{site_id}/assistant/questions/{question_id}/answer", response_model=AssistantQuestionOut)
async def answer_question(
    site_id: uuid.UUID, question_id: uuid.UUID, payload: AssistantQuestionAnswer, user: CurrentUser, db: DB
) -> AssistantQuestion:
    site = await _owned_site(db, user.tenant_id, site_id)
    question = await _owned_question(db, user, site, question_id)

    settings = settings_from_row(site.assistant)
    if len(settings.qa) >= MAX_QA:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You already have {MAX_QA} written answers. Remove one first.")
    settings.qa.append(QA(q=payload.question or question.question, a=payload.answer))
    # Whole-value assignment (not in-place edit) so SQLAlchemy sees the JSONB change.
    site.assistant = settings.model_dump()
    question.status = "answered"
    db.add(site)
    saved = await crud.save(db, question)
    # The assistant's knowledge is rebuilt from the site, so drop its cache now:
    # the very next shopper gets the new answer.
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    return saved


@router.patch("/sites/{site_id}/assistant/questions/{question_id}", response_model=AssistantQuestionOut)
async def update_question(
    site_id: uuid.UUID, question_id: uuid.UUID, payload: AssistantQuestionPatch, user: CurrentUser, db: DB
) -> AssistantQuestion:
    site = await _owned_site(db, user.tenant_id, site_id)
    question = await _owned_question(db, user, site, question_id)
    question.status = payload.status
    return await crud.save(db, question)


@router.delete("/sites/{site_id}/assistant/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(site_id: uuid.UUID, question_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    question = await _owned_question(db, user, site, question_id)
    await crud.delete(db, question)
