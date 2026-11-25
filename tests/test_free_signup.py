"""Signing up creates a Free store, one per phone number. No trial exists."""

import asyncio
import secrets
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app import ai, crud, free_signup, oauth_server, worker
from app.api import trial
from app.db import SessionLocal
from app.models import Site, Tenant, User
from tests.conftest import _cleanup


def _number() -> str:
    """A Bangladesh-shaped number no other account is using."""
    return "019" + f"{secrets.randbelow(10**8):08d}"


@pytest.fixture
async def signups(monkeypatch):
    """Stands in for the Redis-held signup, so /trial/complete can be called
    directly. Every tenant it creates is deleted afterwards."""
    pending: dict[str, dict] = {}
    made: list[str] = []
    emails: list[str] = []

    def add(phone: str, token: str | None = None) -> str:
        suffix = uuid.uuid4().hex[:12]
        token = token or suffix
        email = f"test-{suffix}@softune-test-fixtures.dev"
        emails.append(email)
        pending[token] = {
            "email": email, "password_hash": "x", "full_name": "Test User", "email_verified": True,
            "shop_name": f"test-fixture-{suffix}", "phone": phone, "category": None, "tagline": None,
        }
        return token

    async def load(signup_token, **kw):
        return dict(pending[signup_token])

    async def nothing(*args, **kw):
        return None

    monkeypatch.setattr(trial, "_load_pending", load)
    monkeypatch.setattr(trial, "_save_pending", nothing)
    monkeypatch.setattr(trial.queue, "publish", nothing)
    monkeypatch.setattr(trial.cache, "drop", nothing)
    monkeypatch.setattr(trial.cache, "invalidate_site", nothing)
    real_create = crud.create_tenant_owner_and_site

    async def remember(*args, **kw):
        user, site = await real_create(*args, **kw)
        made.append(str(user.tenant_id))
        return user, site

    monkeypatch.setattr(trial.crud, "create_tenant_owner_and_site", remember)
    add.made = made
    add.pending = pending
    yield add
    await _cleanup(made)


def _complete(client, token: str):
    return client.post("/trial/complete", json={"signup_token": token, "template_key": "aurora"})


async def test_signing_up_creates_a_published_free_store(client, signups):
    token = signups(_number())

    res = await _complete(client, token)

    assert res.status_code == 200, res.text
    async with SessionLocal() as db:
        tenant = await db.get(Tenant, uuid.UUID(signups.made[0]))
        site = (await db.execute(select(Site).where(Site.tenant_id == tenant.id))).scalar_one()
    assert tenant.plan == "free"
    assert tenant.trial_started_at is None and tenant.trial_expires_at is None
    assert tenant.plan_renews_at is None
    assert tenant.onboarding_free_images_remaining == 5  # the sign-up AI images
    assert site.status == "published"
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["plan_info"]["key"] == "free"


async def test_a_second_free_store_on_the_same_number_is_refused_however_it_is_typed(client, signups):
    number = _number()
    assert (await _complete(client, signups(number))).status_code == 200

    for typed in (number, f"+880 {number[1:5]}-{number[5:]}", f"880{number[1:]}"):
        res = await _complete(client, signups(typed))
        assert res.status_code == 409, (typed, res.text)
        assert "already has a Free store" in res.json()["detail"]

    assert len(signups.made) == 1


async def test_two_signups_sent_together_with_one_number_make_one_store(client, signups):
    number = _number()
    first, second = signups(number), signups(number)

    results = await asyncio.gather(_complete(client, first), _complete(client, second))

    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    assert len(signups.made) == 1


async def test_the_details_step_says_so_before_the_last_step(client, signups):
    number = _number()
    assert (await _complete(client, signups(number))).status_code == 200
    token = signups(_number())

    res = await client.patch(
        "/trial/details", json={"signup_token": token, "shop_name": "Another", "phone": number}
    )

    assert res.status_code == 409, res.text


async def test_a_paying_account_with_the_same_number_does_not_block(account_factory):
    paid = await account_factory("growth")
    number = _number()
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.tenant_id == uuid.UUID(paid.tenant_id)).values(phone=number))
        await db.commit()
    async with SessionLocal() as db:
        await free_signup.ensure_phone_available(db, number)  # does not raise

        # The same account, once it is on Free, does.
        await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(paid.tenant_id)).values(plan="free"))
        with pytest.raises(HTTPException) as refused:
            await free_signup.ensure_phone_available(db, number)
        assert refused.value.status_code == 409
        await db.rollback()


@pytest.mark.parametrize(
    "typed, key",
    [
        ("01712345678", "1712345678"),
        ("+880 1712-345678", "1712345678"),
        ("8801712345678", "1712345678"),
        ("1712345678", "1712345678"),
        ("12345", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_phone_key_ignores_how_the_number_was_typed(typed, key):
    assert free_signup.phone_key(typed) == key


def test_nothing_expires_or_deletes_a_trial_any_more():
    for gone in ("sweep_expired_trials", "notify_ended_trials", "delete_expired_trials_stmt"):
        assert not hasattr(worker, gone), gone
    tenant = Tenant(plan="trial", status="active", trial_expires_at=None)
    assert oauth_server.tenant_allowed(tenant)
    assert not oauth_server.tenant_allowed(Tenant(plan="free", status="suspended"))
    assert not oauth_server.tenant_allowed(Tenant(plan="demo", status="active"))


def test_the_dashboard_ai_is_told_about_free_not_a_trial():
    facts = ai._softune_facts()
    assert "There is no free trial." in facts
    assert "Free plan: ৳0" in facts and "50 orders a month" in facts
    assert "৳14,388 for 12 months" in facts
    assert "Free trial:" not in facts
