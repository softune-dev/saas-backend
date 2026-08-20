"""Register, login, refresh, me."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.config import settings
from app.db import get_db
from app.models import Tenant, User
from app.schemas import (
    ChangePasswordIn,
    LoginIn,
    MeOut,
    MeUpdate,
    RefreshIn,
    RegisterIn,
    TenantBusinessUpdate,
    TenantOut,
    TokenOut,
    UserOut,
)
from app.security import (
    CurrentUser,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
DB = Annotated[AsyncSession, Depends(get_db)]


def _tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.tenant_id, user.role),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: DB) -> TokenOut:
    """Create a workspace (tenant) and its first user, then log them straight in.

    Signing up creates BOTH rows in one transaction. If the user insert fails,
    the tenant must not survive as an orphan — so there is exactly one commit at
    the end, not one per row.
    """
    existing = await db.execute(select(User.id).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        # Deliberately explicit here. Some argue this leaks which emails are
        # registered, but a vague error on a SIGNUP form is a support nightmare
        # ("it just says something went wrong"). The real protection against
        # enumeration belongs on /login, which is vague below.
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email exists.")

    # Ensure a unique workspace slug: "Acme Co" -> acme-co, acme-co-2, ...
    base = crud.slugify(payload.workspace_name, "workspace")
    slug = base
    for n in range(2, 100):
        taken = await db.execute(select(Tenant.id).where(Tenant.slug == slug))
        if not taken.scalar_one_or_none():
            break
        slug = f"{base}-{n}"

    tenant = Tenant(slug=slug, name=payload.workspace_name)
    db.add(tenant)
    # flush (not commit) sends the INSERT so tenant.id is populated, while the
    # transaction stays open and still rolls back as one unit.
    await db.flush()

    user = User(
        tenant_id=tenant.id,
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role="owner",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _tokens(user)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, db: DB) -> TokenOut:
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

    user.last_login_at = func.now()
    await db.commit()
    await db.refresh(user)
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshIn, db: DB) -> TokenOut:
    """Swap a refresh token for a fresh pair.

    This endpoint DOES hit the database (unlike normal request auth), because
    it is the natural checkpoint to confirm the account still exists and is
    active — the check we skip on every other request for speed.
    """
    claims = decode_token(payload.refresh_token, expect="refresh")
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


@router.patch("/me", response_model=MeOut)
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


@router.patch("/tenant", response_model=TenantOut)
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


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
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
