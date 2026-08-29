"""Read-only store analytics — real numbers, honestly scoped.

Real visitor/traffic data now exists too (see migrations/037_page_views.sql
and PageView) — visits/unique_visitors/conversion_rate are computed from
actual page-load beacons the storefront fires (app/api/public.py's
log_page_view), not estimated. A site with the beacon not yet reaching it
(an old cached storefront build, JS disabled) just reports 0 visits rather
than a fabricated number.

Profit is real too, but partial by construction: it's computed only from
order items with a cost snapshot (see migrations/036_product_cost_price.sql),
so a merchant who's never set a product's Cost Price gets an honestly
incomplete number, never a wrong one from assuming zero cost —
cost_data_coverage_percent tells the frontend (and the merchant) how much of
current-period revenue that profit figure actually covers.

Everything here is computed from Order/OrderItem/Product/Category/PageView,
scoped to one site via crud.get_scoped, same as every other tenant-owned
resource.
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud
from app.db import get_db
from app.models import Category, Order, OrderItem, PageView, Product, Site
from app.security import CurrentUser

log = logging.getLogger(__name__)

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
    visits: StatOut
    conversion_rate: StatOut
    profit: StatOut
    # What share of current-period revenue actually had cost data behind it
    # — profit is computed only from items with a cost snapshot, so a
    # merchant who's never set Cost Price on any product sees 0% here and
    # knows the profit number above is meaningless, not silently wrong.
    cost_data_coverage_percent: float
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


async def _unique_visitors(
    db: AsyncSession, site_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    """Distinct session_id count for [start, end) — one aggregate query,
    not a row pull, since only the count is needed here."""
    return (
        await db.execute(
            select(func.count(func.distinct(PageView.session_id))).where(
                PageView.site_id == site_id,
                PageView.created_at >= start,
                PageView.created_at < end,
            )
        )
    ).scalar_one()


@router.get("", response_model=AnalyticsOut)
async def get_analytics(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    weeks: Annotated[int, Query(ge=1, le=26)] = 8,
) -> AnalyticsOut:
    site = await crud.get_scoped(db, Site, user.tenant_id, site_id)

    # This endpoint computes several aggregate queries over every order in
    # the window (see below) on EVERY request — expensive, and the numbers
    # don't meaningfully change second-to-second. Cache per (site, weeks),
    # dropped on any commerce write via cache.invalidate_dashboard (see
    # app/cache.py's module docstring for why invalidation is per-site, not
    # per-endpoint).
    cache_key = cache.dashboard_key(str(site_id), "analytics", str(weeks))
    cached = await cache.get_json(cache_key)
    if cached is not None:
        try:
            return AnalyticsOut(**cached)
        except ValidationError:
            # A cached response from before a field was added/renamed here
            # (e.g. this endpoint gaining visits/profit) — same "cache must
            # never break a request" rule as the rest of this file's cache
            # usage, just applied to a schema mismatch instead of a Redis
            # outage. Falls through and recomputes fresh below.
            log.warning("analytics cache: stale schema for %s, recomputing", cache_key)

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

    def profit_of(rows: list[Order]) -> tuple[int, int, int]:
        """Returns (profit_cents, cost_known_revenue_cents, total_revenue_cents).

        Profit is computed only over line items that have a cost snapshot —
        an item with none is excluded from both profit and "known" revenue,
        never assumed to have zero cost (that would overstate profit)."""
        profit = 0
        known_revenue = 0
        total_revenue = 0
        for o in rows:
            if o.status in _LOST_STATUSES:
                continue
            for item in o.items:
                total_revenue += item.total_cents
                if item.cost_price_cents_snapshot is not None:
                    known_revenue += item.total_cents
                    profit += item.total_cents - item.cost_price_cents_snapshot * item.quantity
        return profit, known_revenue, total_revenue

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

    cur_profit, cur_known_rev, cur_total_rev = profit_of(current)
    prev_profit, _prev_known_rev, _prev_total_rev = profit_of(previous)
    cost_data_coverage_percent = (
        round(cur_known_rev / cur_total_rev * 100, 1) if cur_total_rev else 0.0
    )

    cur_unique = await _unique_visitors(db, site_id, current_start, now)
    prev_unique = await _unique_visitors(db, site_id, previous_start, current_start)
    cur_conversion = round(cur_kept / cur_unique * 100, 1) if cur_unique else 0.0
    prev_conversion = round(prev_kept / prev_unique * 100, 1) if prev_unique else 0.0

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

    result = AnalyticsOut(
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
        visits=StatOut(count=cur_unique, change_percent=_pct_change(cur_unique, prev_unique)),
        conversion_rate=StatOut(
            percent=cur_conversion,
            change_percent=_pct_change(int(cur_conversion * 10), int(prev_conversion * 10)),
        ),
        profit=StatOut(cents=cur_profit, change_percent=_pct_change(cur_profit, prev_profit)),
        cost_data_coverage_percent=cost_data_coverage_percent,
        revenue_curve=curve,
        category_shares=category_shares,
        best_sellers=best_sellers,
        sales_report=sales_report,
    )
    await cache.set_json(cache_key, result.model_dump(mode="json"))
    return result
