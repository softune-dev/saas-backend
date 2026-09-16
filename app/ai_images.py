"""AI image generation — Gemini's image-native models, and the real
purchasable credit ledger that pays for them.

WHY A SEPARATE MODULE FROM app/ai.py: that file's Gemini calls are all
text-in/text-or-JSON-out on one shared cheap model (settings.gemini_model,
Flash-Lite) gated by a free daily counter. Image generation is a genuinely
different cost shape — real, meaningfully different per-call cost across
three tiers (confirmed live against the real API before these tiers were
chosen: default resolution vs generationConfig.imageConfig.imageSize="4K"
on the same model roughly triples the bytes returned, and the Pro/"Nano
Banana Pro" model costs noticeably more again for a real quality jump) — so
it needs its own model config, its own credit currency, and its own ledger,
not a shared daily cap that would let one expensive image call and one
free-tier chat message cost the same "1 unit".

CREDIT MATH (see app/config.py's own settings for the numbers): raw Google
cost per image was ~৳8 / ৳18 / ৳28 across the three tiers; credits are
priced at roughly 5-6x that (1 credit = ৳10 face value), which is
standard/high_res/premium = 5/10/15 credits. Never round DOWN when
adjusting these — that's a margin cut, not a rounding convenience.

Nothing here executes a Cloudinary upload — a generated image is returned
to the caller as raw bytes (base64 over the wire) and stays that way until
the merchant explicitly saves it (see app/api/ai_images.py's
save_to_gallery), so nobody's storage quota fills up with images they
never kept.
"""

import base64
import logging
import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AiImageCreditTransaction, Tenant

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_RETRY_STATUS_CODES = {429, 503}
_RETRY_DELAYS_SECONDS = [1.0, 3.0]

ImageTier = str  # "standard" | "high_res" | "premium" — see TIER_CONFIG keys


def _tier_config() -> dict[str, dict]:
    """Built from settings, not a module-level constant, so a changed env
    var takes effect without a code edit — these are exactly the kind of
    business numbers (model id, credit price) that get tuned after launch.
    """
    return {
        "standard": {
            "model": settings.ai_image_model_standard,
            "credits": settings.ai_image_credits_standard,
            "image_size": None,
            "label": {"en": "Standard", "bn": "স্ট্যান্ডার্ড"},
        },
        "high_res": {
            "model": settings.ai_image_model_standard,
            "credits": settings.ai_image_credits_high_res,
            "image_size": "4K",
            "label": {"en": "High-res 4K", "bn": "হাই-রেজ 4K"},
        },
        "premium": {
            "model": settings.ai_image_model_premium,
            "credits": settings.ai_image_credits_premium,
            "image_size": "4K",
            "label": {"en": "Premium 4K", "bn": "প্রিমিয়াম 4K"},
        },
    }


def tier_credit_cost(tier: str) -> int:
    config = _tier_config().get(tier)
    if config is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown image tier")
    return config["credits"]


def list_tiers() -> list[dict]:
    return [
        {"id": tier_id, "credits": cfg["credits"], "label": cfg["label"]}
        for tier_id, cfg in _tier_config().items()
    ]


async def _post_gemini(http_client: httpx.AsyncClient, url: str, *, params: dict, json: dict) -> httpx.Response:
    """Same short-retry-on-transient-overload shape as app/ai.py's own
    _post_gemini — kept as a separate copy rather than importing that
    module's underscore-prefixed helper across a file boundary."""
    res = await http_client.post(url, params=params, json=json)
    for delay in _RETRY_DELAYS_SECONDS:
        if res.status_code not in _RETRY_STATUS_CODES:
            break
        import asyncio

        await asyncio.sleep(delay)
        res = await http_client.post(url, params=params, json=json)
    return res


async def generate_image(
    prompt: str,
    tier: str,
    reference_images: list[tuple[bytes, str]] | None = None,
    aspect_ratio: str = "1:1",
) -> tuple[bytes, str]:
    """Calls Gemini's image-native model for `tier` and returns
    (image_bytes, mime_type). `reference_images` is [(bytes, mime_type), ...]
    — a merchant's own product photo(s) to ground an edit or a "cover photo
    from my product" generation; each becomes an inlineData part ahead of
    the text prompt, exactly the shape Gemini expects for image+text input.
    `aspect_ratio` (see schemas.ImageAspectRatio for the real supported set,
    confirmed live against the API) maps straight onto
    generationConfig.imageConfig.aspectRatio. Raises HTTPException on any
    failure — callers must refund credits already charged before this call
    if it fails (see app/api/ai_images.py's generate/edit routes for that
    ordering).
    """
    if not settings.gemini_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI image generation isn't configured yet — set GEMINI_API_KEY.",
        )
    config = _tier_config().get(tier)
    if config is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown image tier")

    parts: list[dict] = []
    for image_bytes, mime_type in reference_images or []:
        parts.append(
            {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}
        )
    parts.append({"text": prompt})

    image_config: dict = {"aspectRatio": aspect_ratio}
    if config["image_size"]:
        image_config["imageSize"] = config["image_size"]
    body: dict = {"contents": [{"parts": parts}], "generationConfig": {"imageConfig": image_config}}

    url = GEMINI_URL.format(model=config["model"])
    try:
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            res = await _post_gemini(
                http_client, url, params={"key": settings.gemini_api_key}, json=body
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't reach the AI image service: {exc}"
        ) from exc

    if res.status_code != 200:
        log.warning("gemini image generation failed: %s %s", res.status_code, res.text[:500])
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Image generation failed — please try again."
        )

    data = res.json()
    candidates = data.get("candidates") or []
    for candidate in candidates:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"]), inline.get("mimeType", "image/jpeg")

    # A model that refused (safety block, or just returned text explaining
    # why) still comes back as HTTP 200 with no inlineData part — treat
    # that as a clean failure, not a crash on a missing key.
    log.warning("gemini image generation returned no image part: %s", str(data)[:500])
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "The AI couldn't generate an image for that prompt — try rephrasing it.",
    )


async def get_balance(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    return tenant.ai_image_credits


async def charge_credits(
    db: AsyncSession, tenant_id: uuid.UUID, credits: int, reason: str, reference: str | None = None
) -> int:
    """Deducts `credits` and returns the new balance. Raises 402 (not 403 —
    this isn't an authorization failure, it's "you need to pay") if the
    tenant can't cover it; nothing is deducted in that case. Called BEFORE
    generate_image so a failed generation never leaves a tenant charged —
    see app/api/ai_images.py's routes, which refund_credits on any
    downstream failure after this succeeds.
    """
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    if tenant.ai_image_credits < credits:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Not enough image credits — this needs {credits}, you have {tenant.ai_image_credits}.",
        )
    tenant.ai_image_credits -= credits
    db.add(
        AiImageCreditTransaction(
            tenant_id=tenant_id, delta=-credits, reason=reason,
            balance_after=tenant.ai_image_credits, reference=reference,
        )
    )
    await db.commit()
    return tenant.ai_image_credits


async def refund_credits(
    db: AsyncSession, tenant_id: uuid.UUID, credits: int, reference: str | None = None
) -> int:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    tenant.ai_image_credits += credits
    db.add(
        AiImageCreditTransaction(
            tenant_id=tenant_id, delta=credits, reason="refund",
            balance_after=tenant.ai_image_credits, reference=reference,
        )
    )
    await db.commit()
    return tenant.ai_image_credits


# Credit packs sold through the same manual-bKash-claim pattern as a plan
# purchase (app/api/billing.py's submit_manual_payment) — mirrored on the
# dashboard side in components/ai-image/credit-packs-data.ts, same "hardcode
# both sides, keep them in sync by hand" tradeoff as SWITCHABLE_PLANS/
# PLAN_PRICES_CENTS already accepts. Face-value price for the smallest pack,
# a real (not token) discount on the bigger ones — see this module's own
# top docstring for the margin math these numbers came from.
CREDIT_PACKS: dict[str, dict] = {
    "starter": {"name": "Starter", "credits": 50, "price_taka": 500},
    "popular": {"name": "Popular", "credits": 150, "price_taka": 1400},
    "pro": {"name": "Pro", "credits": 400, "price_taka": 3600},
}


async def grant_credits(
    db: AsyncSession, tenant_id: uuid.UUID, credits: int, reason: str, reference: str | None = None
) -> int:
    """Shared by a confirmed credit-pack purchase and a superadmin manual
    grant — `reason` distinguishes them in the ledger ("purchase" vs
    "grant"), everything else is identical."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    tenant.ai_image_credits += credits
    db.add(
        AiImageCreditTransaction(
            tenant_id=tenant_id, delta=credits, reason=reason,
            balance_after=tenant.ai_image_credits, reference=reference,
        )
    )
    await db.commit()
    return tenant.ai_image_credits
