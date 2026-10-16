"""AI store setup endpoints — see app/ai_site_setup.py for the whole flow and
why it exists. Three steps the dashboard calls in order (plan, image x5,
apply) so the merchant watches the store fill in, and a closed tab can
resume: GET returns exactly where the last run stopped.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_images, ai_log, crud
from app import ai_site_setup as setup
from app.api import ai_images as ai_images_api
from app.api import commerce, payments, sites
from app.config import settings
from app.db import get_db
from app.models import Template
from app.schemas import (
    CategoryCreate,
    GenerateImageIn,
    PaymentConnectIn,
    ProductCreate,
    ProductFeature,
    SiteUpdate,
)
from app.security import CurrentUser

router = APIRouter(prefix="/ai/site-setup", tags=["ai-setup"])
DB = Annotated[AsyncSession, Depends(get_db)]


class SiteSetupIn(BaseModel):
    shop_name: str = Field(min_length=1, max_length=80)
    sells: str = Field(min_length=2, max_length=200)
    category_names: list[str] = Field(default_factory=list, max_length=2)
    product_name: str = Field(min_length=1, max_length=120)
    product_price_taka: int = Field(ge=0, le=10_000_000)
    # A real photo the merchant already uploaded (Cloudinary URL from the
    # dashboard's own uploader). When given it IS the product image, so the
    # free allowance isn't spent drawing something they already have.
    product_photo_url: str | None = Field(default=None, max_length=1000)
    language: Literal["bn", "en"] = "bn"
    notes: str | None = Field(default=None, max_length=400)


class ImageSlotIn(BaseModel):
    slot: Literal["hero", "category_0", "category_1", "product", "why"]


async def _state_out(db: AsyncSession, tenant_id, state: dict) -> dict:
    free = await ai_images.get_onboarding_free_remaining(db, tenant_id)
    return setup.public_state(state, free)


@router.get("")
async def get_setup(user: CurrentUser, db: DB) -> dict:
    tenant = await setup.load_tenant(db, user.tenant_id)
    return await _state_out(db, user.tenant_id, setup.get_state(tenant))


@router.post("/plan")
async def plan_site(payload: SiteSetupIn, user: CurrentUser, db: DB) -> dict:
    tenant = await setup.load_tenant(db, user.tenant_id)
    state = setup.get_state(tenant)
    if state.get("status") == "done":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your store was already set up with AI. Edit it any time from the theme editor.",
        )
    runs = int(state.get("runs", 0))
    if runs >= settings.ai_site_setup_max_runs:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You've used all your AI setup tries. Finish your store from the setup steps or the theme editor.",
        )

    inputs = payload.model_dump()
    inputs["shop_name"] = inputs["shop_name"].strip()
    inputs["sells"] = inputs["sells"].strip()
    inputs["product_name"] = inputs["product_name"].strip()
    inputs["category_names"] = [c.strip() for c in inputs["category_names"] if c and c.strip()][:2]

    # Logged under the existing "generate_text" kind (ai_usage_logs.kind has a
    # CHECK constraint, migrations/072) tagged source=site_setup, so it shows
    # in the superadmin AI page and its totals without a migration.
    async with ai_log.track(
        "generate_text",
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        input={
            "kind": "site_setup",
            "context": {"site_name": inputs["shop_name"], **{k: v for k, v in inputs.items() if k != "product_photo_url"}},
        },
        meta={"source": "site_setup", "run": runs + 1, "model": settings.ai_site_builder_model},
    ) as tracker:
        plan = await setup.generate_plan(inputs)
        tracker.output = {"plan": plan}

    images: dict[str, str] = {}
    if inputs.get("product_photo_url"):
        images["product"] = inputs["product_photo_url"]
    state = {
        "status": "planned",
        "runs": runs + 1,
        "inputs": inputs,
        "plan": plan,
        "images": images,
        "skipped": [],
        # Kept across a re-plan so a retried apply never creates a second set.
        "created": state.get("created") or {},
    }
    await setup.save_state(db, tenant, state)
    return await _state_out(db, user.tenant_id, state)


@router.post("/image")
async def generate_slot_image(payload: ImageSlotIn, user: CurrentUser, db: DB) -> dict:
    tenant = await setup.load_tenant(db, user.tenant_id)
    state = setup.get_state(tenant)
    plan, inputs = state.get("plan"), state.get("inputs")
    if not plan or not inputs:
        raise HTTPException(status.HTTP_409_CONFLICT, "Fill in the setup form first.")
    images = dict(state.get("images") or {})
    skipped = list(state.get("skipped") or [])
    slot = payload.slot

    # Already made (a resumed run, or a double click): never pay twice.
    if images.get(slot) or slot in skipped:
        return {"slot": slot, "url": images.get(slot), "skipped": slot in skipped, **await _state_out(db, user.tenant_id, state)}

    if await ai_images.get_onboarding_free_remaining(db, user.tenant_id) <= 0:
        # Out of free images: this section keeps the template's look. Never
        # falls through to paid credits — this flow is promised as free.
        skipped.append(slot)
        state["skipped"] = skipped
        state["status"] = "building"
        await setup.save_state(db, tenant, state)
        return {"slot": slot, "url": None, "skipped": True, **await _state_out(db, user.tenant_id, state)}

    site = await setup.first_site(db, user.tenant_id)
    kwargs, media_category = setup.image_request(slot, plan, inputs)
    prompt = await ai_images_api.build_generation_prompt(
        db, user.tenant_id, GenerateImageIn(tier="standard", **kwargs)
    )
    async with ai_log.track(
        "image_generate",
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        input={"subject": f"AI store setup: {slot}", "prompt_sent": prompt},
        meta={
            "source": "site_setup",
            "slot": slot,
            "tier": "standard",
            "aspect_ratio": kwargs["aspect_ratio"],
            "free_onboarding": True,
        },
    ) as tracker:
        image_bytes, _mime, remaining = await setup.generate_free_image(
            db, user.tenant_id, prompt, kwargs["aspect_ratio"]
        )
        url = await setup.store_image(site, tenant, image_bytes, media_category)
        tracker.output = {"url": url, "credits_charged": 0, "onboarding_free_remaining": remaining}
        tracker.thumbnail = await ai_log.make_thumbnail(image_bytes)

    images[slot] = url
    state["images"] = images
    state["status"] = "building"
    await setup.save_state(db, tenant, state)
    return {"slot": slot, "url": url, "skipped": False, **await _state_out(db, user.tenant_id, state)}


async def _create_with_unique_slug(create, payload_cls, data: dict):
    """A merchant may already own a category/product with the same name —
    retry once with a short suffix instead of failing the whole setup on a
    409 slug clash."""
    try:
        return await create(payload_cls(**data))
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        base = crud.slugify(data["name"], "item")
        return await create(payload_cls(**{**data, "slug": f"{base}-{uuid.uuid4().hex[:4]}"}))


@router.post("/apply")
async def apply_site(user: CurrentUser, db: DB) -> dict:
    tenant = await setup.load_tenant(db, user.tenant_id)
    state = setup.get_state(tenant)
    plan, inputs = state.get("plan"), state.get("inputs")
    if not plan or not inputs:
        raise HTTPException(status.HTTP_409_CONFLICT, "Fill in the setup form first.")
    images = state.get("images") or {}
    created = dict(state.get("created") or {})
    site = await setup.first_site(db, user.tenant_id)

    # Categories — saved to state right away, so a retry after a later
    # failure reuses them instead of creating duplicates.
    category_ids: list[str] = list(created.get("category_ids") or [])
    if not category_ids:
        for i, cat in enumerate(plan["categories"]):
            row = await _create_with_unique_slug(
                lambda p: commerce.create_category(site.id, p, user, db),
                CategoryCreate,
                {
                    "name": cat["name"],
                    "description": cat["description"] or None,
                    "image_url": images.get(f"category_{i}"),
                },
            )
            category_ids.append(str(row.id))
        created["category_ids"] = category_ids
        state["created"] = created
        await setup.save_state(db, tenant, state)

    product_id: str | None = created.get("product_id")
    if not product_id:
        product = plan["product"]
        row = await _create_with_unique_slug(
            lambda p: commerce.create_product(site.id, p, user, db),
            ProductCreate,
            {
                "name": product["name"],
                "short_description": product["short_description"] or None,
                "description": setup.product_description_html(plan) or None,
                "price_cents": int(product["price_taka"]) * 100,
                "currency": "BDT",
                "stock": 10,
                "category_id": category_ids[0] if category_ids else None,
                "images": [{"url": images["product"]}] if images.get("product") else [],
                "features": [
                    ProductFeature(title=f["title"], description=f["description"])
                    for f in product["features"]
                    if f.get("title")
                ],
            },
        )
        product_id = str(row.id)
        created["product_id"] = product_id
        state["created"] = created
        await setup.save_state(db, tenant, state)

    # A store with no way to pay can't take its first order — Cash on
    # Delivery needs no account or keys, so it's the safe default.
    if not await setup.has_payment_method(db, site):
        await payments.connect_payment(site.id, "cod", PaymentConnectIn(), user, db)

    theme = {**(site.theme or {}), **setup.theme_patch(plan, inputs, images, category_ids, product_id)}
    seo = dict(site.seo or {})
    if plan.get("meta_description") and not seo.get("meta_description"):
        seo["meta_description"] = plan["meta_description"]
    site = await sites.update_site(site.id, SiteUpdate(theme=theme, seo=seo), user, db)

    if site.status != "published":
        try:
            site = await sites.publish_site(site.id, user, db)
        except HTTPException as exc:
            # Publish cooldown: the site was published moments ago, so it's
            # already live — the theme write above revalidates it anyway.
            if exc.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
                raise
    await sites.complete_onboarding(site.id, user, db)

    state["status"] = "done"
    await setup.save_state(db, tenant, state)
    out = await _state_out(db, user.tenant_id, state)
    # Explicit query, not site.template: the handlers above refreshed `site`,
    # and touching an expired relationship in async SQLAlchemy is an implicit
    # lazy load that raises instead of loading.
    template_key = (
        await db.execute(select(Template.key).where(Template.id == site.template_id))
    ).scalar_one_or_none()
    return {
        **out,
        # The dashboard keeps the theme editor's draft in the browser; it
        # re-seeds that draft from this so a later Publish in the editor
        # can't overwrite what the AI just built with a stale local copy.
        "theme": site.theme,
        "template_key": template_key,
        "category_ids": category_ids,
        "product_id": product_id,
    }
