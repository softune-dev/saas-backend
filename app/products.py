"""Product variant validation.

Variants live inside Product.attributes (JSONB) under the "variants" key,
not their own table — a deliberate scope decision (see
docs/TODO_PRODUCT_PAGE_REBUILD.md): stock stays pooled per-product, variants
are just labels with an optional price adjustment, so a migration wasn't
needed to ship this.

Same reasoning as blocks.py's validate_blocks: JSONB has no database-level
shape enforcement, so this validator IS the schema for the "variants" key.
An unvalidated write path here would let malformed data reach a customer's
live storefront, which has to read this same shape back out to render
variant pickers.

Shape:
    {
      "variants": [
        {
          "type": "Size",
          "affectsPrice": false,
          "values": [{"value": "S"}, {"value": "M"}]
        },
        {
          "type": "Weight",
          "affectsPrice": true,
          "values": [{"value": "250g", "priceDeltaCents": 0},
                      {"value": "500g", "priceDeltaCents": 15000}]
        }
      ]
    }
"""

from typing import Any

from fastapi import HTTPException, status

MAX_VARIANT_TYPES = 10
MAX_VALUES_PER_TYPE = 30
MAX_LABEL_LEN = 40

# Per-tenant product cap by plan. Deliberately a plain dict, not a database
# column — same reasoning as app/ai.py's PLAN_AI_DAILY_CAP: pricing isn't
# finalized, so this stays a one-line change. Unrecognized plans fall back
# to DEFAULT_PRODUCT_LIMIT rather than silently unlimited.
PLAN_PRODUCT_LIMIT: dict[str, int] = {
    "demo": 50,
    "starter": 50,
    "growth": 200,
    "business": 500,
}
DEFAULT_PRODUCT_LIMIT = 50


def plan_product_limit(plan: str) -> int:
    return PLAN_PRODUCT_LIMIT.get(plan, DEFAULT_PRODUCT_LIMIT)


def ensure_within_product_limit(current_count: int, plan: str) -> None:
    """Raise if creating one more product would exceed this tenant's plan
    cap. Takes a plain count rather than a db/tenant_id so this stays a pure
    check — callers (the manual create endpoint and the AI create-product
    action) each do their own tenant-scoped count via crud.count_scoped."""
    limit = plan_product_limit(plan)
    if current_count >= limit:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Product limit reached ({limit} on your current plan). Upgrade to add more products.",
        )


def _validate_variant_type(raw: Any, index: int) -> dict:
    where = f"variant type #{index + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be an object")

    name = str(raw.get("type", "")).strip()
    if not name:
        raise ValueError(f"{where}: name is required")
    if len(name) > MAX_LABEL_LEN:
        raise ValueError(f"{where}: name is too long (max {MAX_LABEL_LEN} characters)")

    affects_price = bool(raw.get("affectsPrice"))

    raw_values = raw.get("values")
    if not isinstance(raw_values, list):
        raise ValueError(f"{where} ({name}): values must be a list")
    if len(raw_values) > MAX_VALUES_PER_TYPE:
        raise ValueError(f"{where} ({name}): too many values (max {MAX_VALUES_PER_TYPE})")

    values: list[dict] = []
    for i, v in enumerate(raw_values):
        vwhere = f"{where} ({name}) > value #{i + 1}"
        if not isinstance(v, dict):
            raise ValueError(f"{vwhere}: must be an object")

        label = str(v.get("value", "")).strip()
        if not label:
            raise ValueError(f"{vwhere}: value is required")
        if len(label) > MAX_LABEL_LEN:
            raise ValueError(f"{vwhere}: value is too long (max {MAX_LABEL_LEN} characters)")

        clean_value: dict[str, Any] = {"value": label}
        # Only meaningful (and only stored) when this type affects price —
        # dropping it otherwise keeps stale price data from lingering if a
        # merchant flips the toggle off after entering prices.
        if affects_price:
            delta = v.get("priceDeltaCents", 0)
            try:
                clean_value["priceDeltaCents"] = int(delta)
            except (TypeError, ValueError):
                raise ValueError(f"{vwhere}: price adjustment must be a number") from None
        values.append(clean_value)

    if not values:
        raise ValueError(f"{where} ({name}): at least one value is required")

    return {"type": name, "affectsPrice": affects_price, "values": values}


def validate_variants(raw: Any) -> list[dict]:
    """Validate and normalise the whole variants array."""
    if not isinstance(raw, list):
        raise ValueError("variants must be a list")
    if len(raw) > MAX_VARIANT_TYPES:
        raise ValueError(f"too many variant types (max {MAX_VARIANT_TYPES})")
    return [_validate_variant_type(v, i) for i, v in enumerate(raw)]


def validate_attributes(raw: dict | None) -> dict:
    """Validate a product's attributes dict. Only 'variants' is a structured,
    enforced shape — everything else passes through untouched, since
    attributes is deliberately an open bag for anything else a merchant
    wants to store.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("attributes must be an object")
    clean = dict(raw)
    if "variants" in clean:
        clean["variants"] = validate_variants(clean["variants"])
    return clean
