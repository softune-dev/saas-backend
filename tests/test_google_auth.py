"""Sign in with Google: the ID-token checks in app/google_auth.py.

A locally generated RSA key stands in for Google's signing key, so these
run offline. Each test signs a token the way Google would, then breaks one
thing (audience, issuer, expiry, email_verified, signature) and expects it
to be refused.
"""

import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import google_auth
from app.config import settings

CLIENT_ID = "1234-test.apps.googleusercontent.com"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _google(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(
        google_auth._jwks,
        "get_signing_key_from_jwt",
        lambda _token: types.SimpleNamespace(key=_key.public_key()),
    )


def _token(signing_key=_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1098765",
        "email": "Shop.Owner@Gmail.com",
        "email_verified": True,
        "name": "Shop Owner",
        "picture": "https://lh3.googleusercontent.com/a/x",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256")


async def test_valid_token_gives_lowercased_identity():
    identity = await google_auth.verify(_token())
    assert identity.email == "shop.owner@gmail.com"
    assert identity.sub == "1098765"
    assert identity.full_name == "Shop Owner"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-app.apps.googleusercontent.com"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
    ],
)
async def test_wrong_audience_issuer_or_expired_is_refused(overrides):
    with pytest.raises(HTTPException) as err:
        await google_auth.verify(_token(**overrides))
    assert err.value.status_code == 401


async def test_unverified_email_is_refused():
    with pytest.raises(HTTPException) as err:
        await google_auth.verify(_token(email_verified=False))
    assert err.value.status_code == 401


async def test_token_signed_by_another_key_is_refused():
    with pytest.raises(HTTPException) as err:
        await google_auth.verify(_token(signing_key=_other_key))
    assert err.value.status_code == 401


async def test_off_until_client_id_is_set(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    with pytest.raises(HTTPException) as err:
        await google_auth.verify(_token())
    assert err.value.status_code == 503
