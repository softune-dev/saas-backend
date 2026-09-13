"""Plan-gated feature access — payment gateways, couriers, and theme
switching are "grow into this" features held back on Starter. Trial is
treated the same as Starter (locked), matching app/products.py's
PLAN_PRODUCT_LIMIT: a trial previews the Starter plan exactly, so it must
not preview features Starter itself doesn't have.

Centralized here, not re-derived per caller, so the actual rule (which
plans, which providers) lives in exactly one place.
"""

from fastapi import HTTPException, status

_LOCKED_PLANS = {"starter", "trial"}

# Real payment gateways needing a merchant credential/account. Cash on
# Delivery and Manual (customer pays the merchant's own number, then
# submits a transaction id/screenshot to verify by hand) need no gateway
# account and stay free on every plan — only these four are gated.
GATED_PAYMENT_PROVIDERS = {"bkash", "nagad", "sslcommerz", "rocket"}

# Only these two couriers are connectable on Starter/Trial; every other
# provider (Pathao, RedX, Paperfly, ...) needs an upgrade first.
STARTER_ALLOWED_COURIERS = {"steadfast", "ecourier"}


def ensure_courier_allowed(plan: str, provider: str) -> None:
    if plan in _LOCKED_PLANS and provider not in STARTER_ALLOWED_COURIERS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{provider.title()} isn't available on your current plan. "
            "Upgrade to connect more couriers.",
        )


def ensure_payment_provider_allowed(plan: str, provider: str) -> None:
    if plan in _LOCKED_PLANS and provider in GATED_PAYMENT_PROVIDERS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{provider.title()} isn't available on your current plan. "
            "Upgrade to unlock payment gateways.",
        )


def ensure_theme_switch_allowed(plan: str) -> None:
    if plan in _LOCKED_PLANS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Switching themes isn't available on your current plan. "
            "Upgrade to switch themes.",
        )
