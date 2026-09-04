"""Pure fraud-detection logic — soft-flag evaluation and IP normalization.

Kept separate from app/api/public.py (same reasoning as app/events.py's
round_discounted_cents) so the actual decision logic is testable without a
database or an HTTP request, and so create_public_order doesn't grow any
larger than it already has.

Small-business tier, deliberately: no courier-network data, no ML risk
scoring. Two mechanisms, both already in this codebase's fraud_rules JSONB
shape (migrations/009) but never enforced until now:

- Soft flags (this module): the order IS created, but marked fraud_status
  "flagged" for the merchant to review in the dashboard's Suspicious Orders
  tab. hold_first_high_value and flag_burst_orders.
- Hard blocks (NOT here — see the IP-block middleware in app/main.py and the
  device pending-lock/cooldown checks in app/api/public.py): the order is
  never created at all.
"""

import ipaddress


def normalize_ip(raw: str) -> str | None:
    """Parse and canonicalize an IP string, or None if it isn't one.

    Used both by the dashboard-facing FraudIpBlocklistEntryCreate validator
    and by the IP-block middleware's comparison — same parsing, one place.
    """
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except (ValueError, AttributeError):
        return None


# Fixed at 2+ orders from the same phone within the configured window — the
# merchant only configures the WINDOW (dashboard/components/fraud/fraud-data.ts's
# "windowMinutes" threshold), matching that rule's own UI copy verbatim:
# "If the same phone places 2+ orders inside this window, flag for review."
BURST_ORDER_THRESHOLD = 2


def evaluate_soft_flags(
    *,
    is_first_order: bool,
    total_cents: int,
    prior_orders_in_window: int,
    rules: dict,
) -> tuple[str, str | None]:
    """Decide whether a new order should be flagged for merchant review.

    Pure function: takes precomputed facts (the caller already knows whether
    this is the customer's first order, and how many of their PRIOR orders
    fall inside the configured burst window — not counting this new one),
    returns (fraud_status, fraud_reason).

    `rules` is a site's fraud_rules JSONB, shaped exactly like
    dashboard/components/fraud/fraud-data.ts's FraudRuleState:
    {"hold_first_high_value": {"enabled": bool, "value": <taka>},
     "flag_burst_orders": {"enabled": bool, "value": <window minutes>}}
    — "value" is taka (not cents) for the high-value rule, converted here.

    High-value-first-order is checked before burst — it's the more specific,
    more actionable signal (a first-time customer placing an unusually large
    order), so if both would trip, report that one.
    """
    high_value_rule = (rules or {}).get("hold_first_high_value") or {}
    threshold_taka = high_value_rule.get("value")
    if (
        high_value_rule.get("enabled")
        and is_first_order
        and threshold_taka
        and total_cents >= int(threshold_taka) * 100
    ):
        return "flagged", "high_value_first_order"

    burst_rule = (rules or {}).get("flag_burst_orders") or {}
    if burst_rule.get("enabled") and (prior_orders_in_window + 1) >= BURST_ORDER_THRESHOLD:
        return "flagged", "burst_orders"

    return "clear", None
