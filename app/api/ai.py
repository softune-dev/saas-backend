"""AI theme suggestions — Colors/Brand panels only.

Returns a validated patch of theme settings; never writes anything itself.
The dashboard applies the patch through the same onChange(patch) path every
manual edit already uses, so this endpoint changing a site's look requires
no new write path and no new trust boundary — see app/ai.py's module
docstring for the validation it performs before a value ever gets here.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai, ai_actions, crud
from app.db import get_db
from app.models import Site
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/ai", tags=["ai"])
# General chat sidebar isn't scoped to one site (it's opened from the global
# header), so it gets its own top-level router — tenant isolation still
# holds via CurrentUser, it just never touches a Site row at all.
chat_router = APIRouter(prefix="/ai/chat", tags=["ai"])
# Confirm endpoints for the two things the chat assistant can PROPOSE — see
# app/ai_actions.py's module docstring for why these are separate requests
# from the chat call itself, gated on the merchant's own click.
actions_router = APIRouter(prefix="/ai/actions", tags=["ai"])
DB = Annotated[AsyncSession, Depends(get_db)]


class AISuggestIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)


class AISuggestOut(BaseModel):
    patch: dict


@router.post("/suggest", response_model=AISuggestOut)
async def suggest(
    site_id: uuid.UUID, payload: AISuggestIn, user: CurrentUser, db: DB
) -> AISuggestOut:
    # Resolving the site server-side (never trusting a client-supplied
    # "current settings" blob) is what keeps this tenant-isolated: the
    # context handed to Gemini is this tenant's row, full stop.
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)
    patch = await ai.suggest_theme_patch(
        payload.prompt, site.theme or {}, str(user.tenant_id)
    )
    return AISuggestOut(patch=patch)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class ChatOut(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    pending_action: dict[str, Any] | None = None


@chat_router.post("", response_model=ChatOut)
async def chat(payload: ChatIn, user: CurrentUser, db: DB) -> ChatOut:
    reply, tools_used, pending_action = await ai.chat_reply(
        payload.message,
        [t.model_dump() for t in payload.history],
        str(user.tenant_id),
        db,
    )
    return ChatOut(reply=reply, tools_used=tools_used, pending_action=pending_action)


class SetCategoriesIn(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=30)


class SetCategoriesOut(BaseModel):
    categories: list[dict]


@actions_router.post("/set-categories", response_model=SetCategoriesOut)
async def confirm_set_categories(
    payload: SetCategoriesIn, user: CurrentUser, db: DB
) -> SetCategoriesOut:
    categories = await ai_actions.set_categories(db, user.tenant_id, payload.categories)
    return SetCategoriesOut(categories=categories)


class CreateProductActionIn(BaseModel):
    # Deliberately loose (dict, not the full ProductCreate schema) — this is
    # a chat-gathered draft the merchant already saw and confirmed in the UI,
    # not a raw client form post. app/ai_actions.py does the real validation
    # (variants shape, required name) before anything touches the database.
    product: dict[str, Any]


class CreateProductActionOut(BaseModel):
    product: dict


@actions_router.post("/create-product", response_model=CreateProductActionOut)
async def confirm_create_product(
    payload: CreateProductActionIn, user: CurrentUser, db: DB
) -> CreateProductActionOut:
    product = await ai_actions.create_product(db, user.tenant_id, payload.product)
    return CreateProductActionOut(product=product)


class UpdateProductActionIn(BaseModel):
    # Same "loose dict, real validation happens in ai_actions" reasoning as
    # CreateProductActionIn — product_id/product_name plus whichever fields
    # are actually changing.
    product: dict[str, Any]


class UpdateProductActionOut(BaseModel):
    product: dict


@actions_router.post("/update-product", response_model=UpdateProductActionOut)
async def confirm_update_product(
    payload: UpdateProductActionIn, user: CurrentUser, db: DB
) -> UpdateProductActionOut:
    product = await ai_actions.update_product(db, user.tenant_id, payload.product)
    return UpdateProductActionOut(product=product)
