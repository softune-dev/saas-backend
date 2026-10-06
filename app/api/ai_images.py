"""AI image generation — presets, generate/edit, save-to-gallery, and the
manual credit-purchase claim. See app/ai_images.py for the actual Gemini
call + credit ledger, and app/ai_image_presets.py for the preset registry.

Two-step boundary, same shape as every other write in this app: nothing
here trusts a client-supplied price or credit amount — CREDIT_PACKS and the
per-tier credit costs are both server-side constants (app/ai_images.py).
"""

import base64
import io
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_images, crud, mailer, media
from app.ai_image_presets import (
    ALWAYS_TEXT_FREE_CATEGORIES,
    FREEFORM_QUALITY_BASELINE,
    NO_TEXT_INSTRUCTION,
    PRESET_CATEGORIES,
    TEXT_RENDER_QUALITY,
    freeform_style_hint,
    get_preset,
    presets_by_category,
)
from app.config import settings
from app.db import get_db
from app.media import IMAGE_MAX_BYTES, IMAGE_MAX_MEGAPIXELS, plan_storage_limit, site_storage_used_bytes
from app.models import PaymentClaim, Site, Tenant, User
from app.schemas import (
    CreditPurchaseSubmit,
    EditImageIn,
    GenerateImageIn,
    GeneratedImageOut,
    SaveImageToGalleryIn,
)
from app.security import CurrentUser

router = APIRouter(prefix="/ai/images", tags=["ai-images"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/presets")
async def list_presets(user: CurrentUser) -> dict:
    return {
        "categories": PRESET_CATEGORIES,
        "presets_by_category": presets_by_category(),
        "tiers": ai_images.list_tiers(),
    }


async def _onboarding_allowance_open(db: AsyncSession, tenant_id) -> bool:
    """The free allowance only exists while a site is still mid-onboarding
    (onboarding_completed_at IS NULL). Checked server-side, not just by the
    dashboard hiding the step: once the merchant has finished onboarding,
    any leftover units are dead — "one time, during setup" is the rule, not
    "5 free images forever"."""
    open_site = (
        await db.execute(
            select(Site.id).where(Site.tenant_id == tenant_id, Site.onboarding_completed_at.is_(None)).limit(1)
        )
    ).scalar_one_or_none()
    return open_site is not None


@router.get("/balance")
async def get_balance(user: CurrentUser, db: DB) -> dict:
    free_remaining = 0
    if await _onboarding_allowance_open(db, user.tenant_id):
        free_remaining = await ai_images.get_onboarding_free_remaining(db, user.tenant_id)
    return {
        "balance": await ai_images.get_balance(db, user.tenant_id),
        "onboarding_free_remaining": free_remaining,
    }


async def _business_context_line(db: AsyncSession, tenant_id) -> str:
    """One line of real store context prepended to every generation prompt —
    without this, Gemini has no idea WHOSE product/store it's rendering, so
    a preset like "hero banner for {subject}" produces something generic
    instead of something that actually fits this merchant's business. Reads
    the same site.business JSONB app/ai_tools.py's get_site_info exposes to
    the text assistant; empty fields are just omitted, never invented.
    """
    site = (await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))).scalars().first()
    if site is None:
        return ""
    business = site.business or {}
    name = (business.get("name") or site.name or "").strip()
    description = (business.get("description") or "").strip()
    if not name and not description:
        return ""
    line = f'This image is for "{name}"' if name else "This image is for a Bangladeshi e-commerce store"
    if description:
        line += f", a business that sells {description}"
    line += ". Keep the result on-brand and appropriate for this store — do not invent an unrelated brand name or logo."
    return line


_BENGALI_CHARS = re.compile(r"[ঀ-৿]")


def _clean_copy(value: str | None) -> str:
    """One line, no double quotes — the merchant's own words, kept from
    breaking out of the quoted copy block they're embedded in."""
    return " ".join((value or "").replace('"', "'").split())


def _copy_instruction(payload: GenerateImageIn) -> str:
    """The merchant's exact headline/description/button wording, if they
    gave any. A blank field is "let AI choose" and is simply not mentioned,
    so the preset's text_addon still tells the model to write copy that fits
    the business for every element left blank. Bengali copy gets an explicit
    script instruction — image models otherwise tend to mangle conjuncts or
    swap in Latin letters, which is exactly the failure a Bangla storefront
    can't ship.
    """
    fields = [
        ("headline", _clean_copy(payload.headline)),
        ("supporting line", _clean_copy(payload.description)),
        ("button label", _clean_copy(payload.button_text)),
    ]
    given = [(label, text) for label, text in fields if text]
    if not given:
        return ""
    listed = "; ".join(f'{label} "{text}"' for label, text in given)
    instruction = (
        f"Use EXACTLY this wording, character for character, for these elements: {listed}. "
        "Do not reword, translate, shorten, or add to them. Any text element not "
        "listed here you may still write yourself to fit the business."
    )
    if any(_BENGALI_CHARS.search(text) for _, text in given):
        instruction += (
            " The given copy is in Bengali (Bangla) script: render every letter, "
            "conjunct (juktakkhor), and vowel sign (matra) correctly in a clean, "
            "legible Bengali typeface, and never substitute Latin letters for it."
        )
    return instruction


def _apply_preset(payload: GenerateImageIn) -> str:
    """Builds the base scene prompt (never mentions text) plus, depending on
    payload.include_text, either the preset's own text_addon +
    TEXT_RENDER_QUALITY or NO_TEXT_INSTRUCTION — see app/ai_image_presets.py's
    module docstring for why this split exists (one merchant choice, not a
    fixed per-category rule).
    """
    if payload.preset_id:
        preset = get_preset(payload.preset_id)
        if preset is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown preset")
        subject = (payload.subject or "").strip() or "this product"
        prompt = preset["prompt"].format(subject=subject)
        # A category tile is never a marketing graphic — enforced here too,
        # not just by the frontend hiding the toggle, so a raw API call
        # can't bake text into one either (see ALWAYS_TEXT_FREE_CATEGORIES).
        include_text = payload.include_text and preset["category"] not in ALWAYS_TEXT_FREE_CATEGORIES
        if include_text:
            prompt += f" {preset['text_addon']}"
            copy = _copy_instruction(payload)
            if copy:
                prompt += f" {copy}"
            prompt += f" {TEXT_RENDER_QUALITY}"
        else:
            prompt += f" {NO_TEXT_INSTRUCTION}"
        if payload.prompt and payload.prompt.strip():
            prompt += f"\n\nAdditional instructions from the merchant: {payload.prompt.strip()}"
        return prompt
    if payload.prompt and payload.prompt.strip():
        prompt = payload.prompt.strip()
        # No preset picked — the merchant is generating from their own
        # words. Still worth guiding toward a professional result: match
        # keywords like "event"/"banner"/"social" against a preset
        # category's own composition standards, and always add a baseline
        # quality push either way (see app/ai_image_presets.py's
        # freeform_style_hint/FREEFORM_QUALITY_BASELINE).
        hint = freeform_style_hint(prompt)
        if hint:
            prompt += f" {hint}"
        prompt += f" {FREEFORM_QUALITY_BASELINE}"
        if payload.include_text:
            copy = _copy_instruction(payload)
            if copy:
                prompt += f" {copy}"
            prompt += f" {TEXT_RENDER_QUALITY}"
        else:
            prompt += f" {NO_TEXT_INSTRUCTION}"
        return prompt
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Need a preset or a prompt to generate an image.")


async def _resolve_prompt(db: AsyncSession, tenant_id, payload: GenerateImageIn) -> str:
    prompt = _apply_preset(payload)
    context = await _business_context_line(db, tenant_id)
    return f"{context}\n\n{prompt}" if context else prompt


@router.post("/generate", response_model=GeneratedImageOut)
async def generate(payload: GenerateImageIn, user: CurrentUser, db: DB) -> GeneratedImageOut:
    """Charges credits BEFORE calling Gemini, refunds them if the call
    fails — a tenant is never left charged for an image they never
    received (see app/ai_images.py's charge_credits/refund_credits
    docstrings for why the ordering is this way round, not the reverse).
    """
    prompt = await _resolve_prompt(db, user.tenant_id, payload)

    if payload.use_onboarding_free:
        return await _generate_with_onboarding_free(payload, prompt, user.tenant_id, db)

    credits = ai_images.tier_credit_cost(payload.tier)
    balance = await ai_images.charge_credits(
        db, user.tenant_id, credits, reason="generate", reference=payload.preset_id or "custom"
    )
    try:
        reference_images = [
            (base64.b64decode(r.data_base64), r.mime_type) for r in payload.reference_images
        ]
        image_bytes, mime_type = await ai_images.generate_image(
            prompt, payload.tier, reference_images or None, payload.aspect_ratio
        )
    except Exception:
        await ai_images.refund_credits(db, user.tenant_id, credits, reference="generate-failed")
        raise
    return GeneratedImageOut(
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
        mime_type=mime_type,
        credits_charged=credits,
        balance=balance,
    )


async def _generate_with_onboarding_free(
    payload: GenerateImageIn, prompt: str, tenant_id, db: AsyncSession
) -> GeneratedImageOut:
    """The onboarding "Site images" step's free path: standard tier only,
    only while a site is still mid-onboarding, one atomic claim per image
    (see ai_images.claim_onboarding_free), released again if Gemini fails.
    Never touches ai_image_credits — credits_charged is always 0 here.
    """
    if payload.tier != "standard":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Free onboarding images are generated at standard quality.",
        )
    if not await _onboarding_allowance_open(db, tenant_id):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Your free onboarding images are used up. Upload your own, or buy image credits to keep generating.",
        )
    free_remaining = await ai_images.claim_onboarding_free(db, tenant_id)
    try:
        reference_images = [
            (base64.b64decode(r.data_base64), r.mime_type) for r in payload.reference_images
        ]
        image_bytes, mime_type = await ai_images.generate_image(
            prompt, payload.tier, reference_images or None, payload.aspect_ratio
        )
    except Exception:
        await ai_images.release_onboarding_free(db, tenant_id)
        raise
    return GeneratedImageOut(
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
        mime_type=mime_type,
        credits_charged=0,
        balance=await ai_images.get_balance(db, tenant_id),
        onboarding_free_remaining=free_remaining,
    )


@router.post("/edit", response_model=GeneratedImageOut)
async def edit(payload: EditImageIn, user: CurrentUser, db: DB) -> GeneratedImageOut:
    """An edit costs the same as generating fresh at that tier — no
    cheaper "just a tweak" price, so there's no way to game the tier
    pricing into a discount loop (see app/ai_images.py's top docstring).
    """
    credits = ai_images.tier_credit_cost(payload.tier)
    balance = await ai_images.charge_credits(db, user.tenant_id, credits, reason="edit")
    try:
        context = await _business_context_line(db, user.tenant_id)
        instruction = payload.prompt
        instruction += f" {TEXT_RENDER_QUALITY}" if payload.include_text else f" {NO_TEXT_INSTRUCTION}"
        prompt = f"{context}\n\n{instruction}" if context else instruction
        source_bytes = base64.b64decode(payload.source_image.data_base64)
        image_bytes, mime_type = await ai_images.generate_image(
            prompt, payload.tier, [(source_bytes, payload.source_image.mime_type)], payload.aspect_ratio
        )
    except Exception:
        await ai_images.refund_credits(db, user.tenant_id, credits, reference="edit-failed")
        raise
    return GeneratedImageOut(
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
        mime_type=mime_type,
        credits_charged=credits,
        balance=balance,
    )


@router.post("/save-to-gallery")
async def save_to_gallery(payload: SaveImageToGalleryIn, user: CurrentUser, db: DB) -> dict:
    """Nothing is auto-saved on generate — a merchant only spends storage
    quota on images they actually kept, so this is a deliberate, separate
    action, not a side effect of generate/edit. Same size/dimension/
    storage-quota checks POST /sites/{id}/media applies to a real upload
    (this endpoint has no UploadFile to lean on, since the bytes came from
    Gemini, not a browser file input, so the checks are repeated here
    rather than shared — see app/media.py's own IMAGE_MAX_BYTES/
    IMAGE_MAX_MEGAPIXELS, the same limits either path enforces).
    """
    site = (
        await db.execute(select(Site).where(Site.tenant_id == user.tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No site found for this account")

    try:
        image_bytes = base64.b64decode(payload.image.data_base64)
    except Exception as exc:  # noqa: BLE001 - any decode failure means "not valid base64"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid image data") from exc

    if len(image_bytes) > IMAGE_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"This image is {len(image_bytes) / 1024 / 1024:.1f}MB. Cloudinary's limit "
            f"is {IMAGE_MAX_BYTES // 1024 // 1024}MB.",
        )
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
    except Exception as exc:  # noqa: BLE001 - any decode failure means "not a real image"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This doesn't look like a valid image.") from exc
    megapixels = (width * height) / 1_000_000
    if megapixels > IMAGE_MAX_MEGAPIXELS:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"This image is {width}×{height} ({megapixels:.1f} megapixels), over "
            f"Cloudinary's {IMAGE_MAX_MEGAPIXELS} megapixel limit.",
        )

    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    limit = plan_storage_limit(tenant.plan)
    used = site_storage_used_bytes(site.subdomain)
    if used + len(image_bytes) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Saving this would put you over your {tenant.plan} plan's "
            f"{limit // 1024 // 1024}MB storage limit. Delete unused media or upgrade your plan.",
        )

    return await run_in_threadpool(
        media.upload_image, image_bytes, subdomain=site.subdomain, category=payload.category
    )


@router.post("/purchase-credits", status_code=status.HTTP_202_ACCEPTED)
async def submit_credit_purchase(payload: CreditPurchaseSubmit, user: CurrentUser, db: DB) -> dict:
    """Same self-serve "I already sent the money" boundary as
    app/api/billing.py's submit_manual_payment — persists a PaymentClaim
    (kind="image_credits", migrations/069) so app/api/public.py's
    bkash_sms_webhook has something to match trx_id against once the real
    deposit SMS arrives, same as a plan purchase. The email is still sent
    as a human-readable fallback for the case the SMS never arrives.
    """
    pack = ai_images.CREDIT_PACKS.get(payload.pack_id)
    if pack is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown credit pack")

    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    claim = PaymentClaim(
        tenant_id=user.tenant_id,
        kind="image_credits",
        pack_id=payload.pack_id,
        credits=pack["credits"],
        amount_cents=pack["price_taka"] * 100,
        sender_number=payload.sender_number.strip(),
        trx_id=payload.trx_id.strip(),
        note=payload.note.strip() if payload.note else None,
    )
    await crud.save(db, claim)
    owner = (
        await db.execute(select(User).where(User.tenant_id == user.tenant_id, User.role == "owner"))
    ).scalars().first()

    subject, html_body, text_body = mailer.credit_purchase_submitted_email(
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
