"""What a plan without the paid features still gets to see of its own orders.

A locked feature answers 403 (app/plan_gate.py). These are the softer cuts:
the orders list stays open but short, and an order stays readable but without
the fields that belong to a locked feature. Kept out of commerce.py because
that file is already over the size limit.

Nothing here changes a row. Upgrading brings everything back at once.
"""

from datetime import UTC, datetime, timedelta, timezone

from app import plans
from app.models import Order
from app.schemas import OrderOut

# Months are the merchant's, not UTC's: without this, an order placed before
# 6 am on the 1st would count to the month before. A fixed offset because
# Bangladesh has no daylight saving, and zoneinfo needs an OS timezone
# database that a Windows machine does not have.
DHAKA = timezone(timedelta(hours=6))


def order_window_start(now: datetime | None = None) -> datetime:
    """Midnight in Dhaka on the first day of the previous month: the oldest
    moment an orders list reaches back to on a plan without full_analytics."""
    local = (now or datetime.now(UTC)).astimezone(DHAKA)
    this_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # The day before the 1st is always in the previous month, whatever its
    # length and whatever the year.
    return (this_month - timedelta(days=1)).replace(day=1)


def order_out(order: Order | OrderOut, plan: str) -> OrderOut:
    """The order as this plan may see it. The buyer's name, phone and address
    always stay: without them the merchant could not deliver.

    Works on a copy, never on the ORM row, where a None would be saved."""
    out = OrderOut.model_validate(order)
    hidden: dict[str, None] = {}
    if not plans.has_feature(plan, "fraud"):
        hidden |= dict.fromkeys(("ip_address", "device_id", "fraud_status", "fraud_reason"))
    if not plans.has_feature(plan, "customers"):
        # The link to the customer record is what groups a buyer's orders
        # into a history, which is the feature itself.
        hidden["customer_id"] = None
    return out.model_copy(update=hidden)
