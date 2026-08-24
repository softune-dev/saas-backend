"""Auth flow: account creation, login, refresh, logout, me.

POST /auth/register no longer exists — public self-signup was closed (this
is a paid-only service; accounts are created directly via
crud.create_tenant_owner_and_site, same path scripts/create_account.py uses
after payment is received). These tests call that function directly instead
of over HTTP, since there's no longer a public endpoint to exercise for
account creation itself — everything downstream (login, refresh, logout,
/me) is still real HTTP surface and tested as such.
"""

import uuid

from fastapi import HTTPException

from app.crud import create_tenant_owner_and_site
from app.db import SessionLocal
from tests.conftest import _cleanup


async def _fresh(client) -> tuple[dict, str]:
    suffix = uuid.uuid4().hex[:12]
    email = f"auth-{suffix}@softune-test-fixtures.dev"
    password = "test-password-123"
    async with SessionLocal() as db:
        user, _site = await create_tenant_owner_and_site(
            db,
            email=email,
            password=password,
            workspace_name=f"Auth WS {suffix}",
            plan="demo",
            template_key="aurora",
            site_name=f"Auth Site {suffix}",
            subdomain=f"auth-fixture-{suffix}",
            full_name="Auth Test",
        )
    login = await client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    payload = {"email": email, "password": password}
    return payload, login.json()["access_token"]


async def test_account_creation_returns_working_login(client):
    payload, token = await _fresh(client)
    body = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert body.status_code == 200
    assert body.json()["user"]["email"] == payload["email"]
    assert body.json()["user"]["role"] == "owner"
    await _cleanup([body.json()["tenant"]["id"]])


async def test_account_creation_creates_tenant_with_slug_and_plan(client):
    _, token = await _fresh(client)
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    tenant = me.json()["tenant"]
    assert tenant["slug"]
    assert tenant["plan"] == "demo"
    await _cleanup([tenant["id"]])


async def test_duplicate_email_is_rejected(client):
    payload, token = await _fresh(client)

    raised = False
    async with SessionLocal() as db:
        try:
            await create_tenant_owner_and_site(
                db,
                email=payload["email"],
                password=payload["password"],
                workspace_name="Duplicate WS",
                plan="demo",
                template_key="aurora",
                site_name="Duplicate Site",
                subdomain=f"dup-{uuid.uuid4().hex[:12]}",
            )
        except HTTPException as exc:
            raised = True
            assert exc.status_code == 409

    assert raised, "expected HTTPException(409) for a duplicate email"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_email_is_case_insensitive(client):
    """The citext column should treat Bob@x and bob@x as the same account.
    Without citext this test fails and you get duplicate accounts in production."""
    payload, token = await _fresh(client)

    raised = False
    async with SessionLocal() as db:
        try:
            await create_tenant_owner_and_site(
                db,
                email=payload["email"].upper(),
                password=payload["password"],
                workspace_name="Case WS",
                plan="demo",
                template_key="aurora",
                site_name="Case Site",
                subdomain=f"case-{uuid.uuid4().hex[:12]}",
            )
        except HTTPException as exc:
            raised = True
            assert exc.status_code == 409
    assert raised, "citext should have treated the uppercased email as a duplicate"

    login = await client.post(
        "/auth/login",
        json={"email": payload["email"].upper(), "password": payload["password"]},
    )
    assert login.status_code == 200

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_login_succeeds_and_fails_correctly(client):
    payload, token = await _fresh(client)

    good = await client.post(
        "/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert good.status_code == 200
    assert good.json()["access_token"]

    bad = await client.post(
        "/auth/login", json={"email": payload["email"], "password": "wrong-password"}
    )
    assert bad.status_code == 401

    missing = await client.post(
        "/auth/login",
        json={"email": "nobody-here@softune-test-fixtures.dev", "password": "test-password-123"},
    )
    assert missing.status_code == 401
    # Identical message for both failures — see the comment in app/api/auth.py
    # about not leaking which emails are registered.
    assert bad.json()["detail"] == missing.json()["detail"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_refresh_returns_new_tokens(client):
    suffix = uuid.uuid4().hex[:12]
    email = f"refresh-{suffix}@softune-test-fixtures.dev"
    password = "test-password-123"
    async with SessionLocal() as db:
        await create_tenant_owner_and_site(
            db,
            email=email,
            password=password,
            workspace_name=f"Refresh {suffix}",
            plan="demo",
            template_key="aurora",
            site_name=f"Refresh Site {suffix}",
            subdomain=f"refresh-fixture-{suffix}",
        )
    login = await client.post("/auth/login", json={"email": email, "password": password})
    tokens = login.json()

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]

    # The new access token must actually work.
    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    assert me.status_code == 200
    await _cleanup([me.json()["tenant"]["id"]])


async def test_access_token_rejected_at_refresh_endpoint(client):
    """Mirror of the isolation test: token types must not be interchangeable."""
    _, token = await _fresh(client)
    response = await client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_logout_revokes_both_tokens(client):
    """Real regression test for S2/S3 (see QA_TRACKING.md) — logout must
    actually invalidate the tokens, not just return 204."""
    suffix = uuid.uuid4().hex[:12]
    email = f"logout-{suffix}@softune-test-fixtures.dev"
    password = "test-password-123"
    async with SessionLocal() as db:
        await create_tenant_owner_and_site(
            db,
            email=email,
            password=password,
            workspace_name=f"Logout {suffix}",
            plan="demo",
            template_key="aurora",
            site_name=f"Logout Site {suffix}",
            subdomain=f"logout-fixture-{suffix}",
        )
    login = await client.post("/auth/login", json={"email": email, "password": password})
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me_before = await client.get("/auth/me", headers=headers)
    tenant_id = me_before.json()["tenant"]["id"]
    assert me_before.status_code == 200

    logout = await client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=headers,
    )
    assert logout.status_code == 204

    # The just-revoked access token must now be rejected.
    me_after = await client.get("/auth/me", headers=headers)
    assert me_after.status_code == 401

    # The just-revoked refresh token must also be rejected.
    refresh_after = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh_after.status_code == 401

    await _cleanup([tenant_id])
