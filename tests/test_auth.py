"""Auth flow: register, login, refresh, me."""

import uuid

from tests.conftest import _cleanup


async def _fresh(client) -> tuple[dict, str]:
    suffix = uuid.uuid4().hex[:12]
    payload = {
        "email": f"auth-{suffix}@example.test",
        "password": "test-password-123",
        "full_name": "Auth Test",
        "workspace_name": f"Auth WS {suffix}",
    }
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return payload, response.json()["access_token"]


async def test_register_returns_working_tokens(client):
    payload, token = await _fresh(client)
    body = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert body.status_code == 200
    assert body.json()["user"]["email"] == payload["email"]
    assert body.json()["user"]["role"] == "owner"
    await _cleanup([body.json()["tenant"]["id"]])


async def test_register_creates_tenant_with_slug(client):
    _, token = await _fresh(client)
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    tenant = me.json()["tenant"]
    assert tenant["slug"]
    assert tenant["plan"] == "demo"
    await _cleanup([tenant["id"]])


async def test_duplicate_email_is_rejected(client):
    payload, token = await _fresh(client)
    again = await client.post("/auth/register", json=payload)
    assert again.status_code == 409

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_email_is_case_insensitive(client):
    """The citext column should treat Bob@x and bob@x as the same account.
    Without citext this test fails and you get duplicate accounts in production."""
    payload, token = await _fresh(client)
    upper = {**payload, "email": payload["email"].upper()}
    assert (await client.post("/auth/register", json=upper)).status_code == 409

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
        json={"email": "nobody-here@example.test", "password": "test-password-123"},
    )
    assert missing.status_code == 401
    # Identical message for both failures — see the comment in app/api/auth.py
    # about not leaking which emails are registered.
    assert bad.json()["detail"] == missing.json()["detail"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    await _cleanup([me.json()["tenant"]["id"]])


async def test_refresh_returns_new_tokens(client):
    suffix = uuid.uuid4().hex[:12]
    reg = await client.post(
        "/auth/register",
        json={
            "email": f"refresh-{suffix}@example.test",
            "password": "test-password-123",
            "workspace_name": f"Refresh {suffix}",
        },
    )
    tokens = reg.json()

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


async def test_short_password_is_rejected(client):
    response = await client.post(
        "/auth/register",
        json={
            "email": f"short-{uuid.uuid4().hex[:8]}@example.test",
            "password": "abc",
            "workspace_name": "Short",
        },
    )
    assert response.status_code == 422  # Pydantic min_length


async def test_invalid_email_is_rejected(client):
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "test-password-123",
              "workspace_name": "Bad Email"},
    )
    assert response.status_code == 422
