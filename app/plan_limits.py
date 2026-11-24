"""Product and event caps, enforced race-free. Every create path calls this.

WHY A LOCK
----------
"Count the rows, then insert" is a check-then-act race: two requests at 9
products both count 9, both pass, and the tenant ends on 11. So the check
takes a row lock on the TENANT first. The second request blocks on that lock
until the first one commits, then counts again, sees 10 and is refused.

The lock lives exactly as long as the transaction. Every caller calls this
right before its insert and the insert goes through crud.save, which commits —
that commit is what releases the lock. So callers must not await anything slow
(Gemini, Cloudinary, an outbound HTTP call) between this and crud.save.

WHY "NO KEY UPDATE" AND NOT PLAIN "UPDATE"
------------------------------------------
Inserting a product takes a shared lock on its tenant row (the foreign key).
A plain FOR UPDATE would conflict with that, so it would also freeze every
shopper's order insert while one merchant adds a product. FOR NO KEY UPDATE
lets those inserts through and still makes two creators wait for each other.
(It also waits for an ordinary UPDATE of the same tenant row, such as a plan
change; those are brief.)

Plans with no limit (None) never take the lock: there is nothing to race for.

Scope is the TENANT (all of a tenant's sites together), as it always was.
"""

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, events, plan_gate, products
from app.models import Event, Product, Tenant

Kind = Literal["product", "event"]


def _limit(kind: Kind, plan: str) -> int | None:
    return products.plan_product_limit(plan) if kind == "product" else events.plan_event_limit(plan)


async def ensure_room_and_lock(db: AsyncSession, tenant_id: uuid.UUID, kind: Kind) -> None:
    """Raise the plan's 403 if one more `kind` would exceed the cap; otherwise
    return with the tenant row locked until this transaction ends."""
    plan = await plan_gate.tenant_plan(db, tenant_id)  # live, never the token
    if _limit(kind, plan) is None:
        return

    # The plan is re-read under the lock, so an upgrade that landed while we
    # waited is honoured rather than refused on a stale value.
    plan = (
        await db.execute(
            select(Tenant.plan).where(Tenant.id == tenant_id).with_for_update(key_share=True)
        )
    ).scalar_one()

    model = Product if kind == "product" else Event
    count = await crud.count_scoped(db, model, tenant_id)
    if kind == "product":
        products.ensure_within_product_limit(count, plan)
    else:
        events.ensure_within_event_limit(count, plan)
