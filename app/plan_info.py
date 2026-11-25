"""What /auth/me tells the dashboard about the tenant's plan.

The dashboard shell calls /auth/me on every load, so this costs ONE extra
round trip: a single SELECT whose columns are scalar subqueries, instead of
five separate counts. Storage is left out on purpose: it needs a Cloudinary
call, and the media page already returns it.

It must never take /auth/me down. Plan name, features and limits come from
app/plans.py and need no query; if the usage query fails we log it and send
usage as null, and the dashboard just hides the meters.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import order_limit, plans
from app.models import Event, Product, Site, TenantOrderMonth
from app.schemas import PlanInfoOut, PlanLimitsOut, PlanUsageOut

log = logging.getLogger(__name__)


async def _usage(db: AsyncSession, tenant_id: uuid.UUID) -> PlanUsageOut:
    def count(model):
        return (
            select(func.count()).select_from(model)
            .where(model.tenant_id == tenant_id).scalar_subquery()
        )

    # The month row only exists once an order was counted: coalesce so a
    # tenant with no orders yet reads 0, not null.
    month = TenantOrderMonth.month == order_limit.current_month()

    def month_col(col):
        return func.coalesce(
            select(col).where(TenantOrderMonth.tenant_id == tenant_id, month)
            .scalar_subquery(),
            0,
        )

    row = (
        await db.execute(
            select(
                count(Product), count(Event), count(Site),
                month_col(TenantOrderMonth.orders), month_col(TenantOrderMonth.blocked),
            )
        )
    ).one()
    return PlanUsageOut(
        products=row[0], events=row[1], stores=row[2],
        orders_this_month=row[3], blocked_this_month=row[4],
    )


async def build(db: AsyncSession, tenant) -> PlanInfoOut:
    plan = plans.get_plan(tenant.plan)
    try:
        usage = await _usage(db, tenant.id)
    except Exception:  # noqa: BLE001 - /auth/me must survive a usage failure
        log.warning("plan usage failed for tenant %s", tenant.id, exc_info=True)
        usage = None
        try:
            await db.rollback()  # a failed statement poisons the transaction
        except Exception:  # noqa: BLE001 - dead connection; get_db closes it
            pass
    return PlanInfoOut(
        key=plan.key,
        name=plan.name,
        features=sorted(plan.features),
        limits=PlanLimitsOut(
            products=plan.products,
            storage_bytes=plan.storage_bytes,
            orders_per_month=plan.orders_per_month,
            stores=plan.stores,
            ai_per_day=plan.ai_per_day,
            events=plan.events,
        ),
        usage=usage,
    )
