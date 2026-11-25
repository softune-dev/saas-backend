"""The one plan table: every limit, feature and price lives here.

Before this, product caps, storage, AI, event limits and prices each had
their own dict in a different module (products, media, ai, ai_tools, events,
invoices) and they drifted — ai_tools had no "trial" entry, so a trial was
told 80 AI requests a day instead of 15. Those modules now read from PLANS
through get_plan(), so changing a number is a one-line edit in one file.

This module imports only the standard library and fastapi, so any other
module (including app/ai.py's import chain) can use it without a cycle.

Gating rule of thumb: anything a merchant cannot do on their plan is a
FEATURE here and is refused with ensure_feature(), which raises a structured
403 the dashboard turns into an upgrade prompt. Numeric limits (products,
events, storage, AI per day) keep their own plain-string messages.
"""

from dataclasses import dataclass

from fastapi import HTTPException, status

FEATURES: frozenset[str] = frozenset(
    {
        "customers", "pos", "dropship", "seo", "fraud", "domain", "ai",
        "chatgpt", "theme_switch", "gateways", "ecourier", "all_couriers",
        "full_analytics", "no_badge",
    }
)


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price_cents: int  # integer cents, CLAUDE.md rule 7
    products: int | None  # None = unlimited
    storage_bytes: int  # per site, see app/media.py
    orders_per_month: int | None  # None = unlimited
    stores: int
    ai_per_day: int  # 0 = AI not included
    events: int | None  # None = unlimited, which every plan is
    features: frozenset[str]


_MB = 1024 * 1024
_GB = 1024 * _MB

# Every plan has the same payment gateways. Kept as a feature so that gating
# them again would be a change to this table only.
_EVERY_PLAN = frozenset({"gateways"})
# Plans are cumulative: each paid tier is the one below it plus a few more.
_STARTER_FEATURES = _EVERY_PLAN | frozenset(
    {
        "customers", "pos", "seo", "fraud", "domain", "ai", "chatgpt",
        "theme_switch", "ecourier", "full_analytics", "no_badge",
    }
)
_GROWTH_FEATURES = _STARTER_FEATURES | {"all_couriers", "dropship"}

PLANS: dict[str, Plan] = {
    p.key: p
    for p in (
        Plan("free", "Free", 0, 10, 100 * _MB, 50, 1, 0, None, _EVERY_PLAN),
        Plan("starter", "Starter", 49_900, 50, 500 * _MB, None, 1, 30, None, _STARTER_FEATURES),
        Plan("growth", "Growth", 149_900, 500, 2 * _GB, None, 1, 100, None, _GROWTH_FEATURES),
        Plan("business", "Business", 399_900, None, 3 * _GB, None, 2, 150, None, _GROWTH_FEATURES),
        # A trial previews Starter exactly, so it must not preview a feature
        # Starter doesn't give: it keeps theme switching locked as it always
        # did, with Starter's other limits and the old 15 AI requests a day.
        Plan(
            "trial", "Trial", 0, 50, 500 * _MB, None, 1, 15, None,
            _STARTER_FEATURES - {"theme_switch"},
        ),
        # The shared read-only demo account: everything unlocked.
        Plan("demo", "Demo", 0, 50, 800 * _MB, None, 1, 50, None, FEATURES),
    )
}
PAID_PLANS: tuple[str, ...] = ("starter", "growth", "business")

# A Free store left unused this long is deleted, after warning emails: "free as
# long as you're active". Longer for someone who has paid before.
FREE_INACTIVE_DAYS = 30
FORMER_PAID_INACTIVE_DAYS = 90

# What a merchant pays in total for a period, in integer cents. Written out
# instead of computed from a discount: these are the numbers on the pricing
# page, and an SMS must match one of them to the cent.
BILLING_MONTHS: tuple[int, ...] = (1, 3, 6, 12)
PERIOD_PRICES_CENTS: dict[str, dict[int, int]] = {
    "starter": {1: 49_900, 3: 142_500, 6: 269_400, 12: 478_800},
    "growth": {1: 149_900, 3: 427_500, 6: 809_400, 12: 1_438_800},
    "business": {1: 399_900, 3: 1_139_700, 6: 2_159_400, 12: 3_838_800},
}


def period_price(plan: str, months: int = 1) -> int:
    """Total for the period. A plan nobody pays for costs 0; a period that is
    not sold raises, so it can never become an invoice for a made-up amount."""
    if plan not in PERIOD_PRICES_CENTS:
        return 0
    return PERIOD_PRICES_CENTS[plan][months]


def get_plan(key: str | None) -> Plan:
    """An unrecognised or missing plan is treated as the most restricted one.
    The old per-module dicts fell back to Starter-ish defaults; a typo'd plan
    value should never hand out paid limits."""
    return PLANS.get(key or "", PLANS["free"])


def _check_feature(feature: str) -> None:
    if feature not in FEATURES:
        # A typo'd feature name would otherwise lock (or unlock) everyone
        # silently, so it fails loudly the first time the code path runs.
        raise ValueError(f"Unknown plan feature: {feature!r}")


def has_feature(key: str, feature: str) -> bool:
    _check_feature(feature)
    return feature in get_plan(key).features


def needed_plan(feature: str) -> str:
    """The cheapest paid plan that includes the feature (what to upsell)."""
    _check_feature(feature)
    for key in PAID_PLANS:
        if feature in PLANS[key].features:
            return key
    raise ValueError(f"No paid plan includes {feature!r}")


def ensure_feature(key: str, feature: str) -> None:
    if has_feature(key, feature):
        return
    needed = needed_plan(feature)
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        {
            "code": "plan_locked",
            "feature": feature,
            "needed_plan": needed,
            "message": f"This is available on the {PLANS[needed].name} plan and above.",
        },
    )


# Real payment gateways needing a merchant credential/account. Cash on
# Delivery and Manual (customer pays the merchant's own number, then
# submits a transaction id/screenshot to verify by hand) need no gateway
# account and are never gated. These four follow the "gateways" feature, which
# every plan currently has.
GATED_PAYMENT_PROVIDERS = {"bkash", "nagad", "sslcommerz", "rocket"}


def ensure_courier_allowed(plan: str, provider: str) -> None:
    # Steadfast is open to everyone, eCourier needs "ecourier", every other
    # courier (Pathao, RedX, Paperfly, ...) needs "all_couriers".
    if provider == "steadfast":
        return
    feature = "ecourier" if provider == "ecourier" else "all_couriers"
    if plan == "free":
        # The structured lock, for the upgrade popup. Other plans keep their
        # message.
        ensure_feature(plan, feature)
    if not has_feature(plan, feature):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{provider.title()} isn't available on your current plan. "
            "Upgrade to connect more couriers.",
        )


def ensure_payment_provider_allowed(plan: str, provider: str) -> None:
    if provider in GATED_PAYMENT_PROVIDERS and plan == "free":
        ensure_feature(plan, "gateways")
    if provider in GATED_PAYMENT_PROVIDERS and not has_feature(plan, "gateways"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{provider.title()} isn't available on your current plan. "
            "Upgrade to unlock payment gateways.",
        )


def ensure_theme_switch_allowed(plan: str) -> None:
    if not has_feature(plan, "theme_switch"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Switching themes isn't available on your current plan. "
            "Upgrade to switch themes.",
        )
