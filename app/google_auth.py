"""Sign in with Google: verifies the ID token Google's button hands the browser.

The browser (Google Identity Services) gets a signed JWT from Google and
posts it to /auth/google or /trial/google. We check it ourselves against
Google's published signing keys, so there's no client secret and no extra
round trip to Google per login beyond the cached key fetch:

  - signature: RS256, key from https://www.googleapis.com/oauth2/v3/certs
  - iss: accounts.google.com
  - aud: our GOOGLE_CLIENT_ID (a token minted for another app is rejected)
  - exp: not expired
  - email_verified: Google has confirmed the address, which is why a Google
    sign-in can skip our own email OTP.

Off (every call raises) until GOOGLE_CLIENT_ID is set, and the frontends
hide the button in that case.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.config import settings

_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Caches Google's keys (they rotate rarely); a new key id triggers a refetch.
_jwks = PyJWKClient(_CERTS_URL, cache_keys=True, lifespan=6 * 3600)


@dataclass
class GoogleIdentity:
    sub: str
    email: str
    full_name: str | None
    picture: str | None


def enabled() -> bool:
    return bool(settings.google_client_id.strip())


def _verify_sync(credential: str) -> dict:
    key = _jwks.get_signing_key_from_jwt(credential)
    return jwt.decode(
        credential,
        key.key,
        algorithms=["RS256"],
        audience=settings.google_client_id.strip(),
        issuer=_ISSUERS,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        leeway=30,
    )


async def verify(credential: str) -> GoogleIdentity:
    if not enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in isn't set up yet.")
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Please try again.")
    try:
        # The key fetch is a blocking HTTP call; keep it off the event loop.
        claims = await asyncio.to_thread(_verify_sync, credential)
    except Exception:
        raise invalid from None
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "This Google account has no verified email address."
        )
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=email,
        full_name=(claims.get("name") or None),
        picture=(claims.get("picture") or None),
    )
