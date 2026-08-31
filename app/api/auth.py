"""Register, login, refresh, me."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, mailer, queue, recaptcha
from app.config import settings
from app.db import get_db
from app.models import Tenant, TrustedDevice, User
from app.ratelimit import _client_ip, login_rate_limit, rate_limit
from app.schemas import (
    ChangePasswordIn,
    LoginIn,
    LoginResultOut,
    MeOut,
    MeUpdate,
    RefreshIn,
    TenantBusinessUpdate,
    TenantOut,
    TokenOut,
    UserOut,
    VerifyLoginOtpIn,
)
from app.security import (
    CurrentLoginOtp,
    CurrentUser,
    block_demo_writes,
    create_access_token,
    create_login_otp_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_device_id,
    hash_otp,
    hash_password,
    revoke_all_user_tokens,
    revoke_token,
    verify_password,
)

_LOGIN_OTP_TTL_MINUTES = 10
_LOGIN_OTP_MAX_ATTEMPTS = 5
_TRUSTED_DEVICE_DAYS = 30

# Local instance (not security.py's private _bearer) so /auth/logout can read
# the bearer header without an unauthenticated request 401ing before the
# handler even runs — auto_error=False makes a missing/expired access token
# a no-op here (there's still the refresh token in the body to revoke).
_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])
DB = Annotated[AsyncSession, Depends(get_db)]


def _tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(
            user.id, user.tenant_id, user.role, user.is_superadmin
        ),
        refresh_token=create_refresh_token(
            user.id, user.tenant_id, user.role, user.is_superadmin
        ),
        expires_in=settings.access_token_expire_minutes * 60,
    )


# Public self-signup (POST /register) is intentionally removed: this is a
# paid-only service, and an open registration endpoint on a public domain
# meant anyone could create a free account with no payment step in between.
# Until real billing exists, accounts are created directly by us after
# payment is received — see scripts/create_account.py, which uses the exact
# same crud.create_tenant_and_owner() this endpoint used to call, so nothing
# about what "creating an account" means has changed, only who can trigger it.


async def _queue_login_otp_email(to_email: str, otp: str, full_name: str | None) -> None:
    """Queued, not awaited inline — same reasoning as app/api/leads.py's
    _queue_otp_email (a real SMTP send measured 6-7 seconds)."""
    subject, html_body, text_body = mailer.otp_email(otp, full_name)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": to_email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )


@router.post(
    "/login",
    response_model=LoginResultOut,
    dependencies=[Depends(login_rate_limit)],
)
async def login(payload: LoginIn, request: Request, db: DB) -> LoginResultOut:
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "login", _client_ip(request), payload.recaptcha_v2_token
        )
    )

    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # ONE message for every failure mode — wrong email, wrong password,
    # deactivated account. Distinguishing them tells an attacker which emails
    # are real, turning a password-guessing problem into a list-building one.
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if user is None or not verify_password(payload.password, user.password_hash):
        raise invalid
    if not user.is_active:
        raise invalid

    # Device-remembered login 2FA — "not always, only when needed": a device
    # this user has already OTP-verified skips straight to real tokens; an
    # unrecognized (or no) device_id triggers the OTP challenge instead.
    trusted = False
    if payload.device_id:
        device_hash = hash_device_id(payload.device_id)
        row = (
            await db.execute(
                select(TrustedDevice).where(
                    TrustedDevice.user_id == user.id,
                    TrustedDevice.device_hash == device_hash,
                    TrustedDevice.expires_at > func.now(),
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            trusted = True
            row.last_used_at = func.now()

    if not trusted:
        otp = generate_otp()
        user.login_otp_hash = hash_otp(otp)
        user.login_otp_expires_at = datetime.now(UTC) + timedelta(minutes=_LOGIN_OTP_TTL_MINUTES)
        user.login_otp_attempts = 0
        await db.commit()
        await _queue_login_otp_email(user.email, otp, user.full_name)
        return LoginResultOut(otp_required=True, login_token=create_login_otp_token(user.id))

    user.last_login_at = func.now()
    await db.commit()
    await db.refresh(user)
    tokens = _tokens(user)
    return LoginResultOut(otp_required=False, **tokens.model_dump())


@router.post(
    "/verify-login-otp",
    response_model=LoginResultOut,
    dependencies=[Depends(rate_limit("login-verify-otp", limit=10, window_seconds=600))],
)
async def verify_login_otp(payload: VerifyLoginOtpIn, user_id: CurrentLoginOtp, db: DB) -> LoginResultOut:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account unavailable")

    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect or expired code.")

    if user.login_otp_attempts >= _LOGIN_OTP_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — log in again.")
    user.login_otp_attempts += 1

    if (
        not user.login_otp_hash
        or not user.login_otp_expires_at
        or datetime.now(UTC) > user.login_otp_expires_at
        or not secrets.compare_digest(user.login_otp_hash, hash_otp(payload.otp))
    ):
        await db.commit()
        raise invalid

    user.login_otp_hash = None
    user.login_otp_expires_at = None
    user.login_otp_attempts = 0
    user.last_login_at = func.now()

    if payload.remember_device and payload.device_id:
        device_hash = hash_device_id(payload.device_id)
        existing_device = (
            await db.execute(
                select(TrustedDevice).where(
                    TrustedDevice.user_id == user.id, TrustedDevice.device_hash == device_hash
                )
            )
        ).scalar_one_or_none()
        expires_at = datetime.now(UTC) + timedelta(days=_TRUSTED_DEVICE_DAYS)
        if existing_device:
            existing_device.expires_at = expires_at
            existing_device.last_used_at = func.now()
        else:
            db.add(TrustedDevice(user_id=user.id, device_hash=device_hash, expires_at=expires_at))

    await db.commit()
    await db.refresh(user)
    tokens = _tokens(user)
    return LoginResultOut(otp_required=False, **tokens.model_dump())


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshIn, db: DB) -> TokenOut:
    """Swap a refresh token for a fresh pair.

    This endpoint DOES hit the database (unlike normal request auth), because
    it is the natural checkpoint to confirm the account still exists and is
    active — the check we skip on every other request for speed.
    """
    claims = await decode_token(payload.refresh_token, expect="refresh")
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token") from None

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account unavailable")
    return _tokens(user)


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser, db: DB) -> MeOut:
    """Who am I? The admin panel calls this on load to populate the UI."""
    row = (
        await db.execute(
            select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id)
            .where(User.id == user.user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    db_user, tenant = row
    return MeOut(
        user=UserOut.model_validate(db_user), tenant=TenantOut.model_validate(tenant)
    )


@router.patch("/me", response_model=MeOut, dependencies=[Depends(block_demo_writes)])
async def update_me(payload: MeUpdate, user: CurrentUser, db: DB) -> MeOut:
    """Edits the caller's own profile only — there is no id in the path, so
    there is nothing to scope-check. `exclude_unset=True` means omitting
    full_name leaves it alone rather than clearing it."""
    row = (
        await db.execute(
            select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id)
            .where(User.id == user.user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    db_user, tenant = row
    db_user = crud.apply_updates(db_user, payload.model_dump(exclude_unset=True))
    db_user = await crud.save(db, db_user)
    return MeOut(user=UserOut.model_validate(db_user), tenant=TenantOut.model_validate(tenant))


@router.patch("/tenant", response_model=TenantOut, dependencies=[Depends(block_demo_writes)])
async def update_tenant_business(
    payload: TenantBusinessUpdate, user: CurrentUser, db: DB
) -> TenantOut:
    """Legal/tax identity for the account (Account -> Business details).
    Scoped by user.tenant_id from the token, same as /auth/me — no id in the
    path, so there is nothing to leak by omission."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    # New dict object (not an in-place mutation) so SQLAlchemy's change
    # tracking actually sees the JSONB column as dirty.
    tenant.business = {**tenant.business, **payload.model_dump(exclude_unset=True)}
    tenant = await crud.save(db, tenant)
    return TenantOut.model_validate(tenant)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(block_demo_writes)],
)
async def change_password(payload: ChangePasswordIn, user: CurrentUser, db: DB) -> None:
    db_user = (
        await db.execute(select(User).where(User.id == user.user_id))
    ).scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    if not verify_password(payload.current_password, db_user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    db_user.password_hash = hash_password(payload.new_password)
    await crud.save(db, db_user)
    # A password change is exactly the case access-token statelessness can't
    # cover on its own (see get_principal's tradeoff note) — every other
    # device/tab stays logged in on the old password otherwise, for up to
    # ACCESS_TOKEN_EXPIRE_MINUTES on access tokens and the full 14 days on
    # any refresh token. Kill all of them now; this request's own new tokens
    # (issued after this point) are unaffected since they're issued after.
    await revoke_all_user_tokens(db_user.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshIn,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Revoke this session's access token (from the bearer header) and
    refresh token (from the body) so both stop working immediately instead
    of quietly remaining valid — an access token for up to
    ACCESS_TOKEN_EXPIRE_MINUTES, a refresh token for up to 14 days."""
    if creds is not None:
        try:
            access_claims = await decode_token(creds.credentials, expect="access")
            await revoke_token(
                access_claims["jti"],
                datetime.fromtimestamp(access_claims["exp"], tz=UTC),
            )
        except HTTPException:
            pass  # already invalid/expired — nothing to revoke

    try:
        refresh_claims = await decode_token(payload.refresh_token, expect="refresh")
        await revoke_token(
            refresh_claims["jti"],
            datetime.fromtimestamp(refresh_claims["exp"], tz=UTC),
        )
    except HTTPException:
        pass  # already invalid/expired — nothing to revoke
