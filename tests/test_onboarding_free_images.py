"""One-time free AI image allowance for the onboarding "Site images" step.

Gemini is faked (monkeypatched app.ai_images.generate_image) — everything
else, including the atomic claim, runs against the real database. Needs
migrations/071_onboarding_free_images.sql applied first.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app import ai_images
from app.api.ai_images import _apply_preset, _copy_instruction
from app.config import settings
from app.db import SessionLocal
from app.models import Site, Tenant
from app.schemas import GenerateImageIn

FREE_BODY = {
    "preset_id": "hero-studio-gradient",
    "subject": "handmade leather bags",
    "aspect_ratio": "16:9",
    "tier": "standard",
    "use_onboarding_free": True,
}


@pytest.fixture
def fake_gemini(monkeypatch):
    async def _ok(prompt, tier, reference_images, aspect_ratio):
        _ok.last_prompt = prompt
        return b"fake-image-bytes", "image/png"

    monkeypatch.setattr(ai_images, "generate_image", _ok)
    return _ok


async def _set_free_remaining(tenant_id: str, value: int) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(onboarding_free_images_remaining=value)
        )
        await db.commit()


async def _set_credits(tenant_id: str, value: int) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == tenant_id).values(ai_image_credits=value))
        await db.commit()


async def _balance(account) -> dict:
    return (await account.get("/ai/images/balance")).json()


async def test_new_tenant_starts_with_the_configured_allowance(account):
    body = await _balance(account)
    assert body["onboarding_free_remaining"] == settings.onboarding_free_images
    assert body["balance"] == 0


async def test_free_generation_spends_one_unit_and_never_credits(account, fake_gemini):
    response = await account.post("/ai/images/generate", json=FREE_BODY)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["credits_charged"] == 0
    assert data["balance"] == 0
    assert data["onboarding_free_remaining"] == settings.onboarding_free_images - 1
    assert (await _balance(account))["onboarding_free_remaining"] == settings.onboarding_free_images - 1


async def test_free_generation_is_standard_quality_only(account, fake_gemini):
    response = await account.post("/ai/images/generate", json={**FREE_BODY, "tier": "premium"})
    assert response.status_code == 422
    assert (await _balance(account))["onboarding_free_remaining"] == settings.onboarding_free_images


async def test_exhausted_allowance_answers_402_and_never_charges_credits(account, fake_gemini):
    await _set_free_remaining(account.tenant_id, 0)
    await _set_credits(account.tenant_id, 100)

    response = await account.post("/ai/images/generate", json=FREE_BODY)

    assert response.status_code == 402
    assert response.json()["detail"].startswith("Your free onboarding images")
    assert (await _balance(account))["balance"] == 100, "paid credits must not be used as a fallback"


async def test_failed_generation_gives_the_unit_back(account, monkeypatch):
    async def _boom(*args, **kwargs):
        raise HTTPException(503, "Gemini is down")

    monkeypatch.setattr(ai_images, "generate_image", _boom)

    response = await account.post("/ai/images/generate", json=FREE_BODY)

    assert response.status_code == 503
    assert (await _balance(account))["onboarding_free_remaining"] == settings.onboarding_free_images


async def test_two_racing_claims_cannot_both_take_the_last_unit(account):
    await _set_free_remaining(account.tenant_id, 1)

    async def _claim() -> str:
        async with SessionLocal() as db:
            try:
                await ai_images.claim_onboarding_free(db, account.tenant_id)
                return "won"
            except HTTPException as exc:
                assert exc.status_code == 402
                return "lost"

    results = await asyncio.gather(_claim(), _claim())

    assert sorted(results) == ["lost", "won"]
    async with SessionLocal() as db:
        assert await ai_images.get_onboarding_free_remaining(db, account.tenant_id) == 0


async def test_allowance_closes_once_onboarding_is_completed(account, fake_gemini):
    async with SessionLocal() as db:
        await db.execute(
            update(Site)
            .where(Site.tenant_id == account.tenant_id)
            .values(onboarding_completed_at=datetime.now(UTC))
        )
        await db.commit()

    assert (await _balance(account))["onboarding_free_remaining"] == 0
    response = await account.post("/ai/images/generate", json=FREE_BODY)
    assert response.status_code == 402


async def test_one_tenants_free_images_do_not_touch_anothers(two_accounts, fake_gemini):
    a, b = two_accounts
    assert (await a.post("/ai/images/generate", json=FREE_BODY)).status_code == 200

    assert (await _balance(a))["onboarding_free_remaining"] == settings.onboarding_free_images - 1
    assert (await _balance(b))["onboarding_free_remaining"] == settings.onboarding_free_images


async def test_normal_generation_never_spends_the_free_allowance(account, fake_gemini):
    await _set_credits(account.tenant_id, 50)
    body = {k: v for k, v in FREE_BODY.items() if k != "use_onboarding_free"}

    response = await account.post("/ai/images/generate", json=body)

    assert response.status_code == 200
    assert response.json()["credits_charged"] == 5
    assert (await _balance(account))["onboarding_free_remaining"] == settings.onboarding_free_images


async def test_demo_tenants_get_no_allowance():
    async with SessionLocal() as db:
        from app.crud import create_tenant_owner_and_site

        user, _ = await create_tenant_owner_and_site(
            db,
            email="test-demo-free@softune-test-fixtures.dev",
            password="test-password-123",
            workspace_name="Test demo free",
            plan="demo",
            template_key="aurora",
            site_name="Demo",
            subdomain="test-demo-free-images",
            full_name="Demo",
        )
        tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
        try:
            assert tenant.onboarding_free_images_remaining == 0
        finally:
            await db.delete(tenant)
            await db.commit()


# --- prompt building (pure functions, no DB) --------------------------------


def _payload(**kw) -> GenerateImageIn:
    return GenerateImageIn(preset_id="hero-studio-gradient", subject="bags", **kw)


def test_exact_copy_lands_in_the_prompt():
    prompt = _apply_preset(_payload(headline="Eid Sale", description="Up to 30% off", button_text="Shop now"))
    assert 'headline "Eid Sale"' in prompt
    assert 'supporting line "Up to 30% off"' in prompt
    assert 'button label "Shop now"' in prompt
    assert "EXACTLY" in prompt


def test_blank_copy_means_let_ai_choose():
    prompt = _apply_preset(_payload(headline="  ", description=None, button_text=""))
    assert "EXACTLY" not in prompt
    assert _copy_instruction(_payload()) == ""


def test_copy_is_ignored_when_the_image_has_no_text():
    prompt = _apply_preset(_payload(include_text=False, headline="Eid Sale"))
    assert "Eid Sale" not in prompt


def test_bengali_copy_gets_a_script_instruction():
    prompt = _apply_preset(_payload(headline="ঈদ সেল"))
    assert "Bengali" in prompt
    assert "ঈদ সেল" in prompt


def test_quotes_in_copy_cannot_break_out_of_the_copy_block():
    instruction = _copy_instruction(_payload(headline='Big "SALE"\nnow'))
    assert 'headline "Big \'SALE\' now"' in instruction


def test_new_preset_categories_exist_and_are_reachable():
    from app.ai_image_presets import get_preset, presets_by_category

    grouped = presets_by_category()
    assert len(grouped["why_choose_us"]) >= 4
    assert len(grouped["about"]) >= 4
    assert get_preset("about-hands-at-work")["category"] == "about"
