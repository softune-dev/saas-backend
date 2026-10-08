"""Admin audit log: records every action a platform operator takes in
/superadmin into admin_audit_logs (migrations/076), for the "Audit log" page
and each tenant's history tab.

Written in its own session so it survives a request that later fails or
rolls back, and so auditing a tenant delete still works after the tenant row
is gone. Like the cache and queue helpers (rule 9), it logs and swallows its
own failures: an audit write must never fail the action it describes.
"""

import logging
import uuid
from typing import Any

from app.db import SessionLocal
from sqlalchemy import select

from app.models import AdminAuditLog, User
from app.security import Principal

log = logging.getLogger(__name__)


async def email_of(actor: Principal) -> str:
    """The signed-in admin's email. The request principal only carries ids."""
    try:
        async with SessionLocal() as session:
            email = (await session.execute(select(User.email).where(User.id == actor.user_id))).scalar_one_or_none()
            return email or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


async def _email(session, actor: Principal) -> str:
    email = (await session.execute(select(User.email).where(User.id == actor.user_id))).scalar_one_or_none()
    return email or "unknown"


async def record(
    actor: Principal,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str | None,
    target_label: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        async with SessionLocal() as session:
            session.add(
                AdminAuditLog(
                    actor_id=actor.user_id,
                    actor_email=await _email(session, actor),
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    target_label=target_label,
                    details={k: v for k, v in (details or {}).items() if v is not None},
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        log.warning("couldn't write admin audit log for %s", action, exc_info=True)
