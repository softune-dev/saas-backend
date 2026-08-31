"""Password hashing, JWT tokens, and the current-identity dependency."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Tenant


# ---------------------------------------------------------------------------
#  Shared 6-digit OTP helpers — used by both app/api/leads.py (email
#  verification) and app/api/auth.py (device-remembered login 2FA). Plain
#  SHA-256, not bcrypt: a 6-digit code is low-entropy either way, the real
#  protection is the short expiry + attempt cap each caller enforces
#  itself, not the hash algorithm.
# ---------------------------------------------------------------------------


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def hash_device_id(device_id: str) -> str:
    """Same instinct as hash_otp — a DB leak alone shouldn't hand out
    working trusted-device tokens."""
    return hashlib.sha256(device_id.encode()).hexdigest()

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
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, is_superadmin: bool,
    kind: Literal["access", "refresh"], ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),  # tenant — the isolation key, see note below
        "role": role,
        "sa": is_superadmin,
        "typ": kind,
        "iat": now,
        "exp": now + ttl,
        # Individual-token id — lets a single token be revoked (logout)
        # without touching every other token issued to this user.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(
    user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, is_superadmin: bool = False
) -> str:
    return _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, is_superadmin=is_superadmin,
        kind="access", ttl=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(
    user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, is_superadmin: bool = False
) -> str:
    return _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, is_superadmin=is_superadmin,
        kind="refresh", ttl=timedelta(days=settings.refresh_token_expire_days),
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


def create_login_otp_token(user_id: uuid.UUID) -> str:
    """Issued by POST /auth/login when a device isn't trusted yet, instead
    of real tokens. typ "login_otp" so it can never be presented anywhere
    outside POST /auth/verify-login-otp — carries no role/tenant, same
    reasoning as create_lead_token."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "typ": "login_otp", "iat": now, "exp": now + timedelta(minutes=10)}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


async def get_login_otp_user_id(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code expired — please log in again") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    if payload.get("typ") != "login_otp":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token") from None


CurrentLoginOtp = Annotated[uuid.UUID, Depends(get_login_otp_user_id)]


def create_lead_token(lead_id: uuid.UUID) -> str:
    """A SEPARATE, much narrower credential from create_access_token — typ
    "lead" so it can never be presented to a normal authenticated endpoint
    (get_principal only ever decodes typ "access"). Carries no role/tenant
    at all; a lead isn't a Principal, just a prospect walking a funnel.
    Long-lived (days, not minutes) since a prospect might not finish
    signup -> OTP -> profile -> demo in one sitting.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(lead_id),
        "typ": "lead",
        "iat": now,
        "exp": now + timedelta(days=settings.lead_token_expire_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


async def get_lead_id(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID:
    """Auth dependency for app/api/leads.py's post-signup steps (verify-otp
    already has the lead_id from the signup response; the steps AFTER that
    — profile, demo-access, purchase-request — need this token instead of
    asking the prospect to log in again with no real account to log into).
    """
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired — please sign up again") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    if payload.get("typ") != "lead":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token") from None


CurrentLead = Annotated[uuid.UUID, Depends(get_lead_id)]


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
    is_superadmin: bool = False

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
            is_superadmin=bool(payload.get("sa", False)),
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


def require_superadmin(user: CurrentUser) -> Principal:
    """Dependency for /superadmin/* only. Deliberately 404, not 403 — same
    reasoning as CLAUDE.md's tenant-isolation rule 3: a 403 here would
    confirm to any authenticated (non-superadmin) user that this router
    exists at all, which a 404 doesn't."""
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


SuperAdminUser = Annotated[Principal, Depends(require_superadmin)]


# ---------------------------------------------------------------------------
#  Demo accounts — browse only, never write
# ---------------------------------------------------------------------------
# Handed to prospects so they can click around a real, populated dashboard
# before buying. They must never be able to touch the data (upload media,
# add/edit a category or product, publish theme edits) or run up AI costs
# on someone else's tenant — see app/api/__init__.py, where this is wired
# onto every router that can mutate data.

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DEMO_WRITE_BLOCKED_MESSAGE = (
    "This is a demo account for preview only — changes here aren't saved. "
    "Contact us to get your own site."
)


async def block_demo_writes(
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    """Turns every mutating request (anything but GET/HEAD/OPTIONS) from a
    "demo" plan tenant into a 403. Safe methods return immediately — no
    extra query — since browsing freely is the entire point of a demo
    account; only the plan lookup below costs a request that a write was
    going to pay for anyway.
    """
    if request.method in _SAFE_METHODS:
        return user
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    if tenant.plan == "demo":
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_WRITE_BLOCKED_MESSAGE)
    return user
