"""Event validation, per-tenant plan limits, and discount math.

Same reasoning as app/products.py's PLAN_PRODUCT_LIMIT: a plain dict, not a
database column, because pricing isn't finalized. Unrecognized plans fall
back to DEFAULT_EVENT_LIMIT rather than silently unlimited. These are
tiered to prevent spam (a merchant flooding their homepage with dozens of
"events"), not to cap catalog size the way PLAN_PRODUCT_LIMIT does — hence
much smaller numbers.
"""

from fastapi import HTTPException, status

PLAN_EVENT_LIMIT: dict[str, int] = {
    "demo": 3,
    "trial": 3,
    "starter": 3,
    "growth": 10,
    "business": 25,
}
DEFAULT_EVENT_LIMIT = 3


def plan_event_limit(plan: str) -> int:
    return PLAN_EVENT_LIMIT.get(plan, DEFAULT_EVENT_LIMIT)


def ensure_within_event_limit(current_count: int, plan: str) -> None:
    """Raise if creating one more event would exceed this tenant's plan
    cap. Takes a plain count rather than a db/tenant_id — same shape as
    app/products.py's ensure_within_product_limit."""
    limit = plan_event_limit(plan)
    if current_count >= limit:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Event limit reached ({limit} on your current plan). Upgrade to add more events.",
        )


def round_discounted_cents(price_cents: int, discount_percent: int) -> int:
    """price_cents after an X% discount, integer cents, round-half-up.

    Deliberately NOT round(price_cents * (100 - discount_percent) / 100) —
    that divides as a float (banker's rounding + float drift), which is
    exactly what CLAUDE.md rule 7 forbids for money. Adding 50 before the
    // 100 floor division is the standard round-half-up trick and stays
    pure integer arithmetic throughout.
    """
    return (price_cents * (100 - discount_percent) + 50) // 100
