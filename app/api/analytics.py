"""Read-only store analytics — real numbers, honestly scoped.

No traffic/session tracking exists in this app (no visits table), so there
is no real "conversion rate" to report — that stat is deliberately not part
of this response rather than faked. Refund rate replaces it: it's the one
comparable, fully real metric the order data actually supports.

Everything here is computed from Order/OrderItem/Product/Category, scoped to
one site via crud.get_scoped, same as every other tenant-owned resource.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.db import get_db
from app.models import Category, Order, OrderItem, Product, Site
from app.security import CurrentUser

router = APIRouter(prefix="/sites/{site_id}/analytics", tags=["analytics"])
DB = Annotated[AsyncSession, Depends(get_db)]

# Orders in these statuses didn't result in kept revenue — excluded from
# revenue/AOV/best-sellers/category-share, same as an accountant would.
_LOST_STATUSES = {"cancelled", "refunded"}


class StatOut(BaseModel):
    # Exactly one of cents/count/percent is set per stat — kept separate
    # rather than one overloaded "value" field so the frontend never has to
    # guess what unit a number is in.
    cents: int | None = None
    count: int | None = None
    percent: float | None = None
    change_percent: float | None = None


class CurvePointOut(BaseModel):
    label: str
    revenue_cents: int
    orders: int


class CategoryShareOut(BaseModel):
    name: str
    revenue_cents: int
    percent: float


class BestSellerOut(BaseModel):
    id: str | None
    name: str
    category: str
    image: str
    sold: int
    revenue_cents: int


class SalesReportRowOut(BaseModel):
    period: str
    orders: int
    customers: int
    revenue_cents: int
    refunds_cents: int
    net_cents: int


class AnalyticsOut(BaseModel):
    currency: str
    revenue: StatOut
    orders: StatOut
    aov: StatOut
    refund_rate: StatOut
    revenue_curve: list[CurvePointOut]
    category_shares: list[CategoryShareOut]
    best_sellers: list[BestSellerOut]
    sales_report: list[SalesReportRowOut]


def _pct_change(current: int, previous: int) -> float | None:
    # No baseline to compare against — "up 400%" from a previous zero is
    # meaningless, so this is left unset rather than shown as a number.
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _customer_key(order: Order) -> str:
    c = order.customer or {}
    return str(c.get("email") or c.get("phone") or c.get("name") or order.id)


@router.get("", response_model=AnalyticsOut)
async def get_analytics(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    weeks: Annotated[int, Query(ge=1, le=26)] = 8,
) -> AnalyticsOut:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    now = datetime.now(UTC)
    period = timedelta(weeks=weeks)
    current_start = now - period
    previous_start = now - 2 * period

    # One fetch covering both periods — items eager-load via the Order
    # relationship's lazy="selectin" (see models.py), so this is 2 queries
    # total, not N+1 across every order.
    orders = (
        (
            await db.execute(
                select(Order).where(
                    Order.site_id == site_id, Order.created_at >= previous_start
                )
            )
        )
        .scalars()
        .all()
    )
    current = [o for o in orders if o.created_at >= current_start]
    previous = [o for o in orders if o.created_at < current_start]

    def revenue_of(rows: list[Order]) -> int:
        return sum(o.total_cents for o in rows if o.status not in _LOST_STATUSES)

    def kept_count(rows: list[Order]) -> int:
        return sum(1 for o in rows if o.status not in _LOST_STATUSES)

    cur_revenue, prev_revenue = revenue_of(current), revenue_of(previous)
    cur_kept, prev_kept = kept_count(current), kept_count(previous)
    cur_orders, prev_orders = len(current), len(previous)
    cur_refunded = sum(1 for o in current if o.status == "refunded")
    prev_refunded = sum(1 for o in previous if o.status == "refunded")

    cur_aov = cur_revenue // cur_kept if cur_kept else 0
    prev_aov = prev_revenue // prev_kept if prev_kept else 0
    cur_refund_rate = round(cur_refunded / cur_orders * 100, 1) if cur_orders else 0.0
    prev_refund_rate = round(prev_refunded / prev_orders * 100, 1) if prev_orders else 0.0

    currency = current[0].currency if current else (previous[0].currency if previous else "USD")

    # --- weekly revenue curve, oldest to newest, `weeks` buckets ---
    curve: list[CurvePointOut] = []
    for i in range(weeks):
        bucket_start = current_start + i * timedelta(weeks=1)
        bucket_end = bucket_start + timedelta(weeks=1)
        bucket_orders = [o for o in current if bucket_start <= o.created_at < bucket_end]
        curve.append(
            CurvePointOut(
                label=bucket_start.strftime("%b %d"),
                revenue_cents=revenue_of(bucket_orders),
                orders=len(bucket_orders),
            )
        )

    # --- category share + best sellers, from current-period kept orders' items ---
    kept_current = [o for o in current if o.status not in _LOST_STATUSES]
    product_ids = {
        item.product_id for o in kept_current for item in o.items if item.product_id
    }
    products_by_id: dict[uuid.UUID, Product] = {}
    categories_by_id: dict[uuid.UUID, Category] = {}
    if product_ids:
        prod_rows = (
            (await db.execute(select(Product).where(Product.id.in_(product_ids))))
            .scalars()
            .all()
        )
        products_by_id = {p.id: p for p in prod_rows}
        cat_ids = {p.category_id for p in prod_rows if p.category_id}
        if cat_ids:
            cat_rows = (
                (await db.execute(select(Category).where(Category.id.in_(cat_ids))))
                .scalars()
                .all()
            )
            categories_by_id = {c.id: c for c in cat_rows}

    category_revenue: dict[str, int] = defaultdict(int)
    seller_key = lambda item: str(item.product_id) if item.product_id else f"n:{item.name_snapshot}"  # noqa: E731
    seller_qty: dict[str, int] = defaultdict(int)
    seller_revenue: dict[str, int] = defaultdict(int)
    seller_name: dict[str, str] = {}
    seller_product_id: dict[str, str | None] = {}

    for o in kept_current:
        for item in o.items:
            product = products_by_id.get(item.product_id) if item.product_id else None
            category = (
                categories_by_id.get(product.category_id)
                if product and product.category_id
                else None
            )
            category_revenue[category.name if category else "Uncategorized"] += item.total_cents

            key = seller_key(item)
            seller_qty[key] += item.quantity
            seller_revenue[key] += item.total_cents
            seller_name[key] = item.name_snapshot
            seller_product_id[key] = str(item.product_id) if item.product_id else None

    total_cat_revenue = sum(category_revenue.values())
    category_shares = sorted(
        (
            CategoryShareOut(
                name=name,
                revenue_cents=cents,
                percent=round(cents / total_cat_revenue * 100, 1) if total_cat_revenue else 0.0,
            )
            for name, cents in category_revenue.items()
        ),
        key=lambda c: c.revenue_cents,
        reverse=True,
    )[:8]

    def best_seller_row(key: str, qty: int) -> BestSellerOut:
        pid = seller_product_id[key]
        product = products_by_id.get(uuid.UUID(pid)) if pid else None
        category = categories_by_id.get(product.category_id) if product and product.category_id else None
        image = product.images[0].get("url", "") if product and product.images else ""
        return BestSellerOut(
            id=pid,
            name=seller_name[key],
            category=category.name if category else "Uncategorized",
            image=image,
            sold=qty,
            revenue_cents=seller_revenue[key],
        )

    best_sellers = sorted(
        (best_seller_row(key, qty) for key, qty in seller_qty.items()),
        key=lambda b: b.sold,
        reverse=True,
    )[:5]

    # --- weekly sales report table, newest first ---
    sales_report: list[SalesReportRowOut] = []
    for i in range(weeks - 1, -1, -1):
        bucket_start = current_start + i * timedelta(weeks=1)
        bucket_end = bucket_start + timedelta(weeks=1)
        bucket_orders = [o for o in current if bucket_start <= o.created_at < bucket_end]
        bucket_refunds = sum(o.total_cents for o in bucket_orders if o.status == "refunded")
        bucket_revenue = revenue_of(bucket_orders)
        sales_report.append(
            SalesReportRowOut(
                period=f"{bucket_start.strftime('%b %d')} – {(bucket_end - timedelta(days=1)).strftime('%b %d')}",
                orders=len(bucket_orders),
                customers=len({_customer_key(o) for o in bucket_orders}),
                revenue_cents=bucket_revenue,
                refunds_cents=bucket_refunds,
                net_cents=bucket_revenue - bucket_refunds,
            )
        )

    return AnalyticsOut(
        currency=currency,
        revenue=StatOut(
            cents=cur_revenue, change_percent=_pct_change(cur_revenue, prev_revenue)
        ),
        orders=StatOut(
            count=cur_orders, change_percent=_pct_change(cur_orders, prev_orders)
        ),
        aov=StatOut(cents=cur_aov, change_percent=_pct_change(cur_aov, prev_aov)),
        refund_rate=StatOut(
            percent=cur_refund_rate,
            change_percent=_pct_change(int(cur_refund_rate * 10), int(prev_refund_rate * 10)),
        ),
        revenue_curve=curve,
        category_shares=category_shares,
        best_sellers=best_sellers,
        sales_report=sales_report,
    )
