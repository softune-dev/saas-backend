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


def decode_token(token: str, expect: Literal["access", "refresh"]) -> dict:
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
    return payload


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
    payload = decode_token(creds.credentials, expect="access")
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
