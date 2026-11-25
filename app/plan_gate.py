"""Plan gating for routes: read the tenant's plan live, then apply app/plans.py.

The plan is read from the database on every call, never from the JWT or a
cache: a merchant who upgrades (or is downgraded) must feel it on their very
next request, and a token minted last week must not keep old privileges.
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plans
from app.db import get_db
from app.models import Tenant
from app.security import CurrentUser


async def tenant_plan(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """A tenant reading its OWN plan is not an ownership check (nothing to
    leak), so this is a plain primary-key lookup rather than crud.get_scoped.
    A tenant that no longer exists is a 404."""
    plan = (await db.execute(select(Tenant.plan).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return plan


async def ensure(db: AsyncSession, tenant_id: uuid.UUID, feature: str) -> None:
    plans.ensure_feature(await tenant_plan(db, tenant_id), feature)


def require_feature(feature: str):
    """FastAPI dependency: `dependencies=[Depends(require_feature("seo"))]`.
    An unknown feature raises ValueError here, when the route is declared,
    so a typo stops the app at import instead of failing on one request."""
    if feature not in plans.FEATURES:
        raise ValueError(f"Unknown plan feature: {feature!r}")

    async def _gate(
        user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> None:
        await ensure(db, user.tenant_id, feature)

    return _gate
