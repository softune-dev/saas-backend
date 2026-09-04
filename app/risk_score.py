"""Per-customer risk score — the aggregate view Fraud Protection's individual
rules (phone blocklist, IP blocklist, device rules, Suspicious Orders) don't
give a merchant: not "does this ONE order look bad," but "does this
CUSTOMER, across their whole history, look trustworthy."

Rule-based and transparent, same positioning as everything else in
app/fraud.py — every signal here comes from real columns on Order (delivery
outcome via the Steadfast integration, device_id, ip_address, fraud_status),
never a black-box model. A merchant can see exactly why a score is what it
is, which a small COD-heavy store needs more than a number they can't
question.

Pure functions, no I/O — callers (app/api/customers.py) do the querying and
pass in already-fetched Order rows plus the two facts that need a lookup
against OTHER tables (ip_blocklisted, has_open_duplicate).
"""

from app.models import Order

# Below this many resolved (delivered/cancelled) deliveries, the success
# rate itself is too noisy to weight heavily — 1 cancelled parcel out of 1
# looks identical to "always cancels" and to "bad luck once," and punishing
# a new customer as hard as a repeat offender is the wrong failure mode for
# a small store that wants to keep giving people a chance.
_MIN_RESOLVED_FOR_FULL_WEIGHT = 3


def compute_risk_score(
    *,
    orders: list[Order],
    current_device_id: str | None,
    ip_blocklisted: bool,
    has_open_duplicate: bool,
) -> dict:
    """`orders` is this customer's full linked history, newest first (same
    query get_customer already runs). Returns the signal breakdown plus a
    0-100 score and a Low/Medium/High label — see the docstring above for
    why every signal traces back to a real column, not an inference.
    """
    total_orders = len(orders)
    delivered = sum(1 for o in orders if o.delivery_status == "delivered")
    cancelled = sum(1 for o in orders if o.delivery_status == "cancelled")
    resolved = delivered + cancelled
    delivery_success_rate = (delivered / resolved) if resolved else None

    cod_orders = sum(1 for o in orders if (o.meta or {}).get("payment_method") == "cod")

    confirmed_fraud = any(o.fraud_status == "confirmed_fraud" for o in orders)

    known_device = bool(
        current_device_id
        and any(o.device_id == current_device_id for o in orders)
    )
    courier_history_available = any(o.courier_consignment_id for o in orders)

    score = 100

    if total_orders == 0:
        # No history at all — neither trusted nor distrusted, deliberately
        # landing in the middle of the Medium band rather than defaulting
        # to either extreme for a customer we simply haven't seen yet.
        score = 60
    else:
        if resolved >= _MIN_RESOLVED_FOR_FULL_WEIGHT:
            if delivery_success_rate is not None and delivery_success_rate < 0.5:
                score -= 40
            elif delivery_success_rate is not None and delivery_success_rate < 0.8:
                score -= 15
        elif resolved > 0 and delivery_success_rate is not None and delivery_success_rate < 0.5:
            # Too little data for the full penalty, but a cancelled-heavy
            # start is still worth a small deduction, not a free pass.
            score -= 10

        if confirmed_fraud:
            score -= 50
        if current_device_id and not known_device and total_orders > 0:
            score -= 5

    if ip_blocklisted:
        score -= 40
    if has_open_duplicate:
        score -= 10

    score = max(0, min(100, score))
    label = "Low" if score >= 70 else "Medium" if score >= 40 else "High"

    return {
        "score": score,
        "label": label,
        "signals": {
            "previous_orders": total_orders,
            "delivered": delivered,
            "cancelled": cancelled,
            "delivery_success_rate": (
                round(delivery_success_rate * 100) if delivery_success_rate is not None else None
            ),
            "cod_orders": cod_orders,
            "device_known": known_device if current_device_id else None,
            "ip_blocklisted": ip_blocklisted,
            "has_open_duplicate_order": has_open_duplicate,
            "courier_history_available": courier_history_available,
            "confirmed_fraud_history": confirmed_fraud,
        },
    }
