"""AI store setup endpoints — see app/ai_site_setup.py for the whole flow and
why it exists. Three steps the dashboard calls in order (plan, image x5,
apply) so the merchant watches the store fill in, and a closed tab can
resume: GET returns exactly where the last run stopped.
"""

import asyncio
import functools
import uuid
from contextlib import asynccontextmanager
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

# One setup call at a time per store. Two parallel runs (e.g. the panel closed
# and reopened mid-run, then "Continue" clicked while the first run was still
# going) each saw "no image yet" / "nothing created yet" and BOTH did the work:
# duplicate categories and products, and the 5 free images spent twice. The
# second request now waits, then re-reads the saved state and finds the work
# done. In-process on purpose: the API runs a single uvicorn worker (see
# sites.py), so no cross-process lock is needed.
_LOCKS: dict[uuid.UUID, list] = {}  # tenant_id -> [asyncio.Lock, waiters]


@asynccontextmanager
async def _tenant_lock(tenant_id: uuid.UUID):
    entry = _LOCKS.setdefault(tenant_id, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            _LOCKS.pop(tenant_id, None)


def _one_at_a_time(handler):
    @functools.wraps(handler)  # keeps the signature FastAPI reads its params from
    async def wrapper(*args, **kwargs):
        async with _tenant_lock(kwargs["user"].tenant_id):
            return await handler(*args, **kwargs)

    return wrapper


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
@_one_at_a_time
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
@_one_at_a_time
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


def _slug_for(name: str, fallback: str) -> str:
    """An explicit, collision-proof slug. crud.slugify keeps only a-z0-9, so
    every Bangla name ("কুর্তি", "পায়জামা") collapses to the same fallback and
    a second one hits the unique constraint — and Bangla is the default
    language here, so that would break most setups. A name with no usable
    ASCII gets a short random suffix up front."""
    base = crud.slugify(name, fallback)
    return f"{fallback}-{uuid.uuid4().hex[:6]}" if base == fallback else base


async def _create_with_unique_slug(create, payload_cls, data: dict):
    """Create with a safe slug, and retry once with a suffix if it still
    clashes (the merchant may already own an "Aurora Shirt" slug).

    A 409 means crud.save rolled the session back, which expires EVERY loaded
    ORM object — so `create` must not read attributes off a site/tenant object
    loaded before the call (async SQLAlchemy can't lazy-load them and raises
    MissingGreenlet, which is the 500 this used to cause). Callers pass plain
    ids, and re-load rows after this returns."""
    fallback = "category" if payload_cls is CategoryCreate else "product"
    slug = _slug_for(data["name"], fallback)
    try:
        return await create(payload_cls(**{**data, "slug": slug}))
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        return await create(payload_cls(**{**data, "slug": f"{slug}-{uuid.uuid4().hex[:4]}"}))


@router.post("/apply")
@_one_at_a_time
async def apply_site(user: CurrentUser, db: DB) -> dict:
    tenant = await setup.load_tenant(db, user.tenant_id)
    state = setup.get_state(tenant)
    plan, inputs = state.get("plan"), state.get("inputs")
    if not plan or not inputs:
        raise HTTPException(status.HTTP_409_CONFLICT, "Fill in the setup form first.")
    images = state.get("images") or {}
    created = dict(state.get("created") or {})
    site = await setup.first_site(db, user.tenant_id)
    # Plain values, not ORM attributes: a rolled-back create (slug clash)
    # expires `site` and `tenant`, and reading them again would raise.
    site_id = site.id

    # Categories — each saved to state as soon as it exists, so a retry after
    # a later failure reuses it instead of creating a duplicate.
    category_ids: list[str] = list(created.get("category_ids") or [])
    for i, cat in enumerate(plan["categories"]):
        if i < len(category_ids):
            continue
        row = await _create_with_unique_slug(
            lambda p: commerce.create_category(site_id, p, user, db),
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
        tenant = await setup.load_tenant(db, user.tenant_id)
        await setup.save_state(db, tenant, state)

    product_id: str | None = created.get("product_id")
    if not product_id:
        product = plan["product"]
        row = await _create_with_unique_slug(
            lambda p: commerce.create_product(site_id, p, user, db),
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
        tenant = await setup.load_tenant(db, user.tenant_id)
        await setup.save_state(db, tenant, state)

    # Re-load: any rolled-back create above left these objects expired.
    tenant = await setup.load_tenant(db, user.tenant_id)
    site = await setup.first_site(db, user.tenant_id)

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
