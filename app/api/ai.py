"""AI theme suggestions — Colors/Brand panels only.

Returns a validated patch of theme settings; never writes anything itself.
The dashboard applies the patch through the same onChange(patch) path every
manual edit already uses, so this endpoint changing a site's look requires
no new write path and no new trust boundary — see app/ai.py's module
docstring for the validation it performs before a value ever gets here.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai, ai_actions, ai_forms, ai_tools, chat_credits, crud, mailer
from app.config import settings
from app.db import get_db
from app.models import Site, Tenant, User
from app.schemas import ChatCreditPurchaseSubmit
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
# Usage/credits display — its own router since, like chat, it's tenant-wide,
# not scoped to one site.
usage_router = APIRouter(prefix="/ai", tags=["ai"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _tenant_plan(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Every AI call needs this to know the daily cap — see
    app/ai.py's PLAN_AI_DAILY_CAP. Not scoped through crud.get_scoped since
    a tenant looking up its OWN plan isn't an ownership check the way
    looking up a site/product/order is; there's nothing to leak by a
    tenant reading its own plan field.
    """
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    return tenant.plan


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
    plan = await _tenant_plan(db, user.tenant_id)
    patch = await ai.suggest_theme_patch(
        payload.prompt, site.theme or {}, str(user.tenant_id), plan
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
    plan = await _tenant_plan(db, user.tenant_id)
    reply, tools_used, pending_action = await ai.chat_reply(
        payload.message,
        [t.model_dump() for t in payload.history],
        str(user.tenant_id),
        db,
        plan,
    )
    if pending_action is not None:
        # Attaches whatever the confirm card needs: the field schema + pre-
        # filled values for the generic form types (see app/ai_forms.py's
        # module docstring), a read-only old->new diff for update_product
        # specifically, or nothing at all for a type with neither (e.g.
        # create_ticket, which keeps its own bespoke summary card).
        pending_action = await ai_forms.attach_form(pending_action, db, user.tenant_id)
    return ChatOut(reply=reply, tools_used=tools_used, pending_action=pending_action)


class AIUsageOut(BaseModel):
    used: int
    limit: int
    remaining: int


@usage_router.get("/usage", response_model=AIUsageOut)
async def get_ai_usage(user: CurrentUser, db: DB) -> AIUsageOut:
    """Powers the dashboard header's credits display — a read-only peek at
    today's count, never increments it. See app/ai.py's get_usage.
    """
    plan = await _tenant_plan(db, user.tenant_id)
    usage = await ai.get_usage(str(user.tenant_id), plan)
    return AIUsageOut(**usage)


@usage_router.get("/chat-credits/balance")
async def get_chat_credit_balance(user: CurrentUser, db: DB) -> dict:
    """The REAL, purchased chat-credit balance — separate from the free
    daily count get_ai_usage above shows. See app/chat_credits.py."""
    return {"balance": await chat_credits.get_balance(db, user.tenant_id)}


@usage_router.post("/chat-credits/purchase", status_code=status.HTTP_202_ACCEPTED)
async def submit_chat_credit_purchase(payload: ChatCreditPurchaseSubmit, user: CurrentUser, db: DB) -> dict:
    """Same self-serve "I already sent the money" claim as
    app/api/ai_images.py's submit_credit_purchase — a different currency
    (chat credits, not image credits), same boundary: nothing is stored
    here, the email IS the record, a person verifies trx_id and grants
    credits from Superadmin (grant_chat_credits) afterward.
    """
    pack = chat_credits.CHAT_CREDIT_PACKS.get(payload.pack_id)
    if pack is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown credit pack")

    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    owner = (
        await db.execute(select(User).where(User.tenant_id == user.tenant_id, User.role == "owner"))
    ).scalars().first()

    subject, html_body, text_body = mailer.chat_credit_purchase_submitted_email(
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        owner_name=owner.full_name if owner else None,
        owner_email=owner.email if owner else "—",
        pack_name=pack["name"],
        credits=pack["credits"],
        amount_taka=pack["price_taka"],
        sender_number=payload.sender_number.strip(),
        trx_id=payload.trx_id.strip(),
        note=payload.note.strip() if payload.note else None,
    )
    sent_support = await mailer.send_email(mailer.SUPPORT, subject, html_body, text_body)
    sent_copy = await mailer.send_email(settings.billing_notify_email, subject, html_body, text_body)
    if not sent_support and not sent_copy:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't send this right now — email support@softunebd.com directly with your Transaction ID.",
        )
    return {"received": True}


class SuggestedPromptOut(BaseModel):
    text: str
    icon: str


class SuggestedPromptsOut(BaseModel):
    suggestions: list[SuggestedPromptOut]


@usage_router.get("/suggested-prompts", response_model=SuggestedPromptsOut)
async def get_suggested_prompts(user: CurrentUser, db: DB, context: str = "default") -> SuggestedPromptsOut:
    """Powers the chat sidebar's empty-state suggestion chips — real,
    per-merchant prompts built from the same read-only signals the
    assistant's own tools expose (low stock, missing About/FAQs/SEO, no
    sales yet), not a fixed "honey store" placeholder list. Plain DB reads,
    no Gemini call — never counts against the AI daily cap.
    """
    suggestions = await ai_tools.get_suggested_prompts(db, user.tenant_id, context)
    return SuggestedPromptsOut(suggestions=suggestions)


class GenerateTextIn(BaseModel):
    # kind selects which prompt/required-context rule applies — see
    # app/ai.py's _TEXT_PROMPTS / _TEXT_REQUIRED_CONTEXT.
    kind: str = Field(min_length=1, max_length=60)
    # Free-form facts the merchant already entered elsewhere in the form
    # (product name/category/price, site name, etc.) — never trusted as
    # anything other than copywriting context, never written to the database.
    context: dict[str, Any] = Field(default_factory=dict)
    # Non-empty on a "Regenerate" click — switches the prompt from "write
    # new" to "improve this existing draft" instead of discarding it.
    current_text: str | None = Field(default=None, max_length=5000)


class GenerateTextOut(BaseModel):
    text: str


@usage_router.post("/generate-text", response_model=GenerateTextOut)
async def generate_ai_text(payload: GenerateTextIn, user: CurrentUser, db: DB) -> GenerateTextOut:
    """Powers every "Generate"/"Regenerate" button next to a description-style
    field (product/category descriptions, SEO meta/OG description, About page
    paragraphs). No site_id/tenant row is touched here — the merchant reviews
    and saves the result through the normal field + Save button, same as
    typing it themselves.
    """
    plan = await _tenant_plan(db, user.tenant_id)
    text = await ai.generate_text(
        payload.kind, payload.context, payload.current_text, str(user.tenant_id), plan
    )
    return GenerateTextOut(text=text)


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


class AddCategoryActionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    sort_order: int | None = None


class AddCategoryActionOut(BaseModel):
    category: dict


@actions_router.post("/add-category", response_model=AddCategoryActionOut)
async def confirm_add_category(
    payload: AddCategoryActionIn, user: CurrentUser, db: DB
) -> AddCategoryActionOut:
    """Additive — creates ONE category via crud.save, unlike /set-categories
    which deletes and recreates the whole list. See app/ai_actions.py's
    add_category docstring.
    """
    category = await ai_actions.add_category(
        db, user.tenant_id, payload.name, payload.description, payload.sort_order
    )
    return AddCategoryActionOut(category=category)


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


class UpdateOrderStatusActionIn(BaseModel):
    order_id: str | None = None
    order_number: str | None = None
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class UpdateOrderStatusActionOut(BaseModel):
    order: dict


@actions_router.post("/update-order-status", response_model=UpdateOrderStatusActionOut)
async def confirm_update_order_status(
    payload: UpdateOrderStatusActionIn, user: CurrentUser, db: DB
) -> UpdateOrderStatusActionOut:
    """`status` is renamed to `new_status` only on the way into
    ai_actions.update_order_status — see that function's docstring for why
    (avoids shadowing fastapi's `status` module in that file)."""
    order = await ai_actions.update_order_status(
        db, user.tenant_id,
        order_id=payload.order_id, order_number=payload.order_number,
        new_status=payload.status, notes=payload.notes,
    )
    return UpdateOrderStatusActionOut(order=order)


class CreateEventActionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    discount_percent: int = Field(ge=1, le=90)
    cta_label: str = Field(default="Shop now", max_length=40)
    is_active: bool = False
    is_popup: bool = False
    image_only: bool = False


class CreateEventActionOut(BaseModel):
    event: dict


@actions_router.post("/create-event", response_model=CreateEventActionOut)
async def confirm_create_event(
    payload: CreateEventActionIn, user: CurrentUser, db: DB
) -> CreateEventActionOut:
    event = await ai_actions.create_event(
        db, user.tenant_id,
        name=payload.name, description=payload.description,
        discount_percent=payload.discount_percent, cta_label=payload.cta_label,
        is_active=payload.is_active, is_popup=payload.is_popup, image_only=payload.image_only,
    )
    return CreateEventActionOut(event=event)


class CreateTicketActionIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=60)
    priority: str = "Medium"
    message: str = Field(min_length=1, max_length=5000)


class CreateTicketActionOut(BaseModel):
    ticket: dict


@actions_router.post("/create-ticket", response_model=CreateTicketActionOut)
async def confirm_create_ticket(
    payload: CreateTicketActionIn, user: CurrentUser, db: DB
) -> CreateTicketActionOut:
    ticket = await ai_actions.create_ticket(
        db, user.tenant_id, user.user_id,
        payload.subject, payload.category, payload.priority, payload.message,
    )
    return CreateTicketActionOut(ticket=ticket)
