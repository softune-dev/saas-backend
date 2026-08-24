"""Password hashing, JWT tokens, and the current-identity dependency."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
#  Passwords
# ---------------------------------------------------------------------------
# bcrypt is used directly rather than through passlib: passlib is effectively
# unmaintained and its bcrypt backend emits version-detection warnings with
# bcrypt 4.x. Direct use is three lines and has no such friction.


def hash_password(plain: str) -> str:
    # bcrypt silently ignores everything past byte 72. If we did not truncate
    # explicitly, two different long passwords could hash identically — and the
    # user would never know their 100-char passphrase gained nothing. Schemas
    # also cap password length at 72, so this is belt-and-braces.
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except ValueError:
        # Malformed hash in the database — treat as a failed login, never a 500.
        return False


# ---------------------------------------------------------------------------
#  Tokens
# ---------------------------------------------------------------------------


def _encode(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str,
    kind: Literal["access", "refresh"], ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),  # tenant — the isolation key, see note below
        "role": role,
        "typ": kind,
        "iat": now,
        "exp": now + ttl,
        # Individual-token id — lets a single token be revoked (logout)
        # without touching every other token issued to this user.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    return _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, kind="access",
        ttl=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    return _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, kind="refresh",
        ttl=timedelta(days=settings.refresh_token_expire_days),
    )


async def decode_token(token: str, expect: Literal["access", "refresh"]) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None

    # Without this check an attacker could send a long-lived REFRESH token to a
    # normal endpoint and get 14 days of access from a token meant only for
    # exchanging at /auth/refresh.
    if payload.get("typ") != expect:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    # Deny-list check — fails OPEN on a Redis error (see revoke_token's own
    # comment): a JWT is still stateless in the normal case, this is only an
    # extra veto layer for the two cases that actually need instant effect
    # (logout, password change), not a per-request DB/Redis dependency.
    # One MGET round-trip for both keys, not two sequential GETs — this runs
    # on every authenticated request, so it halves both the normal-case
    # latency and the worst-case delay before falling open if Redis is slow
    # or unreachable (bounded by cache.py's 2s socket timeout, once, not
    # twice in series).
    from app import cache

    try:
        c = cache.client()
        jti = payload.get("jti")
        user_id = payload.get("sub")
        jti_val, revoked_at_raw = await c.mget(
            f"revoked:jti:{jti}" if jti else "revoked:jti:__none__",
            f"revoked:user:{user_id}" if user_id else "revoked:user:__none__",
        )
        if jti and jti_val:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked")

        if revoked_at_raw:
            revoked_at = datetime.fromisoformat(revoked_at_raw)
            issued_at = datetime.fromtimestamp(payload["iat"], tz=UTC)
            if issued_at < revoked_at:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Session no longer valid — please log in again"
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - same fail-open tradeoff as cache.py
        import logging

        logging.getLogger(__name__).warning("token revocation check failed: %s (allowing)", exc)

    return payload


async def revoke_token(jti: str, expires_at: datetime) -> None:
    """Deny-list a single token (logout) until it would have expired anyway
    — no reason to keep the entry around after that."""
    from app import cache

    ttl = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))
    try:
        await cache.client().set(f"revoked:jti:{jti}", "1", ex=ttl)
    except Exception as exc:  # noqa: BLE001 - fail open, same as cache.py elsewhere
        import logging

        logging.getLogger(__name__).warning("failed to revoke token %s: %s", jti, exc)


async def revoke_all_user_tokens(user_id: uuid.UUID) -> None:
    """Bulk-revoke every token issued to this user before right now,
    regardless of each token's own expiry — used for password change and
    account deactivation, the two cases from this module's own tradeoff
    note that actually need to take effect immediately rather than waiting
    out ACCESS_TOKEN_EXPIRE_MINUTES. TTL matches the refresh token's
    lifetime, the longest a stale token could otherwise still be valid."""
    from app import cache

    ttl = settings.refresh_token_expire_days * 86400
    try:
        await cache.client().set(
            f"revoked:user:{user_id}", datetime.now(UTC).isoformat(), ex=ttl
        )
    except Exception as exc:  # noqa: BLE001 - fail open, same as cache.py elsewhere
        import logging

        logging.getLogger(__name__).warning("failed to revoke tokens for user %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
#  Current identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """Who is making this request. `tenant_id` is the tenant isolation key —
    every tenant-scoped query in app/crud.py takes it from here and nowhere else.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")


async def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Auth dependency. Reads identity from the token's claims — no database hit.

    TRADEOFF, chosen deliberately: skipping a per-request `SELECT ... FROM users`
    makes every authenticated endpoint one round-trip faster, which matters
    because it applies to literally every call. The cost is that deactivating a
    user does not take effect until their access token expires. That window is
    bounded by ACCESS_TOKEN_EXPIRE_MINUTES (30 by default).

    If you later need instant revocation, the cheap fix is a Redis deny-list of
    revoked user ids checked here — still no database round-trip.
    """
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = await decode_token(creds.credentials, expect="access")
    try:
        return Principal(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            role=payload.get("role", "member"),
        )
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token") from None


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require_admin(user: CurrentUser) -> Principal:
    """Dependency for endpoints only owners/admins may call."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[Principal, Depends(require_admin)]
