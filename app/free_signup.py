"""One Free store per phone number.

The email is already unique (users.email). The phone is the second check against
one person opening Free store after Free store to get past the product and
order limits. It is matched, not verified by SMS.
"""

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant, User

# Must stay the exact expression of ix_users_phone_last10 (migrations/089), or
# Postgres will scan the whole users table on every sign-up.
_LAST10 = r"right(regexp_replace(coalesce(users.phone, ''), '\D', '', 'g'), 10)"


def phone_key(phone: str | None) -> str:
    """The last ten digits: the part of a Bangladesh mobile number that is the
    same however it was typed (01712..., +880 1712..., 880-1712...). Empty for
    anything too short to be a number, which then never matches."""
    digits = "".join(ch for ch in (phone or "") if ch.isascii() and ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


async def lock_phone(db: AsyncSession, phone: str | None) -> None:
    """Holds other sign-ups with this number until the caller's transaction
    ends, so two submitted together cannot both pass the check below."""
    key = phone_key(phone)
    if key:
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"free-phone:{key}"))))


async def ensure_phone_available(db: AsyncSession, phone: str | None) -> None:
    """409 if a Free store's owner already uses this number. A paying account
    with the same number does not block: they are welcome to another store."""
    key = phone_key(phone)
    if not key:
        return
    taken = await db.execute(
        select(User.id)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(Tenant.plan == "free", User.role == "owner", text(f"{_LAST10} = :phone").bindparams(phone=key))
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This mobile number already has a Free store. Log in to that store, or use another number.",
        )
