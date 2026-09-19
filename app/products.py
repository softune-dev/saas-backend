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
          "values": [{"value": "S"}, {"value": "M", "image": "https://..."}]
        },
        {
          "type": "Color",
          "isColor": true,
          "affectsPrice": false,
          "values": [
            {"value": "Navy", "hex": "#1F2A44", "image": "https://..."},
            {"value": "Rust", "hex": "#B7410E"}
          ]
        },
        {
          "type": "Weight",
          "affectsPrice": true,
          "values": [{"value": "250g", "priceDeltaCents": 0},
                      {"value": "500g", "priceDeltaCents": 15000}]
        }
      ]
    }

`hex` is only meaningful (and only ever set by the dashboard) when the
parent type has `isColor: true` — a real merchant-picked color, not a
guess from the label text; the storefront used to derive a swatch color by
matching the label against a hardcoded color-name dictionary
(lib/color-names.ts), which silently rendered the wrong color for any name
not in that list. `image` works on ANY type's values (Color or otherwise —
e.g. a "Pattern" swatch benefits from its own photo too), and is what lets
the storefront swap the main product photo when that value is selected.

COMBINATIONS — the "stock stays pooled" decision above was reversed
(migrations/070): a product with real Size×Color combinations needs its
own price/compare-at/stock/image per combination, not one number for the
whole product. Lives alongside "variants" under the same "combinations"
key, still JSONB — no new table, since each row is just a cartesian-product
pick of the "variants" values plus four numbers and an optional image/sku.
A product with no "variants" has no "combinations" either and behaves
exactly as before (Product.price_cents/stock/compare_at_cents are the
source of truth). Shape:

    {
      "combinations": [
        {
          "key": "Color:Navy|Size:M",
          "optionValues": {"Color": "Navy", "Size": "M"},
          "sku": "SHIRT-NAVY-M",
          "priceCents": 70000,
          "compareAtCents": 95000,
          "stock": 12,
          "trackStock": true,
          "image": "https://...",
          "isActive": true
        }
      ]
    }

`key` is a stable join of sorted "type:value" pairs — the dashboard
generates it deterministically from the cartesian product of "variants" so
it can match existing rows (and their saved price/stock/image) back up
after an unrelated option edit, and so app/api/public.py's checkout can
look a submitted selection up by exact key instead of a positional index.
"""

import re
from typing import Any

from fastapi import HTTPException, status

MAX_VARIANT_TYPES = 10
MAX_VALUES_PER_TYPE = 30
MAX_LABEL_LEN = 40
MAX_IMAGE_URL_LEN = 500
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Cartesian product of MAX_VARIANT_TYPES x MAX_VALUES_PER_TYPE could in
# theory reach the thousands — a real cap here is what stops a merchant (or
# a buggy dashboard build) from ever submitting a combinations array that
# size, not just the UI choosing not to generate one that big.
MAX_COMBINATIONS = 300
MAX_SKU_LEN = 60

# Per-tenant product cap by plan. Deliberately a plain dict, not a database
# column — same reasoning as app/ai.py's PLAN_AI_DAILY_CAP: pricing isn't
# finalized, so this stays a one-line change. Unrecognized plans fall back
# to DEFAULT_PRODUCT_LIMIT rather than silently unlimited.
PLAN_PRODUCT_LIMIT: dict[str, int] = {
    "demo": 50,
    "trial": 50,  # same as starter — a trial previews the Starter plan exactly
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
    is_color = bool(raw.get("isColor"))

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

        # Only meaningful (and only stored) when the parent type is marked
        # Color — dropping it otherwise means flipping isColor off doesn't
        # leave a stale hex silently attached to a now-plain-text value.
        if is_color:
            hex_value = str(v.get("hex", "")).strip()
            if hex_value:
                if not _HEX_COLOR.match(hex_value):
                    raise ValueError(f"{vwhere}: color must be a 6-digit hex like #1F2A44")
                clean_value["hex"] = hex_value

        # Image works on any value regardless of isColor (a "Pattern" swatch
        # benefits from its own photo too) — real Cloudinary URLs only, same
        # upload pipeline as the product gallery, never a client-picked
        # arbitrary string beyond a sane length.
        image_url = str(v.get("image", "")).strip()
        if image_url:
            if len(image_url) > MAX_IMAGE_URL_LEN:
                raise ValueError(f"{vwhere}: image URL is too long")
            clean_value["image"] = image_url

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

    result: dict[str, Any] = {"type": name, "affectsPrice": affects_price, "values": values}
    if is_color:
        result["isColor"] = True
    return result


def validate_variants(raw: Any) -> list[dict]:
    """Validate and normalise the whole variants array."""
    if not isinstance(raw, list):
        raise ValueError("variants must be a list")
    if len(raw) > MAX_VARIANT_TYPES:
        raise ValueError(f"too many variant types (max {MAX_VARIANT_TYPES})")
    return [_validate_variant_type(v, i) for i, v in enumerate(raw)]


def _validate_combination(
    raw: Any, index: int, options: list[dict], seen_keys: set[str]
) -> dict:
    where = f"variant combination #{index + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be an object")

    key = str(raw.get("key", "")).strip()
    if not key:
        raise ValueError(f"{where}: key is required")
    if key in seen_keys:
        raise ValueError(f"{where}: duplicate combination key {key!r}")
    seen_keys.add(key)

    raw_option_values = raw.get("optionValues")
    if not isinstance(raw_option_values, dict) or not raw_option_values:
        raise ValueError(f"{where} ({key}): optionValues must be a non-empty object")

    # Every {type: value} pair has to be a real, currently-defined option —
    # this is what stops a stale combination (from a value the merchant
    # since renamed or deleted) from silently reaching the storefront with
    # a selection that no longer resolves to anything pickable.
    option_values: dict[str, str] = {}
    for type_name, value_label in raw_option_values.items():
        type_name = str(type_name).strip()
        value_label = str(value_label).strip()
        option = next((o for o in options if o["type"] == type_name), None)
        if option is None:
            raise ValueError(f"{where} ({key}): unknown variant type {type_name!r}")
        if not any(v["value"] == value_label for v in option["values"]):
            raise ValueError(
                f"{where} ({key}): {value_label!r} is not a value of {type_name!r}"
            )
        option_values[type_name] = value_label

    result: dict[str, Any] = {"key": key, "optionValues": option_values}

    sku = str(raw.get("sku", "")).strip()
    if sku:
        if len(sku) > MAX_SKU_LEN:
            raise ValueError(f"{where} ({key}): SKU is too long (max {MAX_SKU_LEN} characters)")
        result["sku"] = sku

    for money_field in ("priceCents", "compareAtCents"):
        value = raw.get(money_field)
        if value is None:
            continue
        try:
            cents = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{where} ({key}): {money_field} must be a number") from None
        if cents < 0:
            raise ValueError(f"{where} ({key}): {money_field} can't be negative")
        result[money_field] = cents

    stock = raw.get("stock", 0)
    try:
        stock = int(stock)
    except (TypeError, ValueError):
        raise ValueError(f"{where} ({key}): stock must be a number") from None
    if stock < 0:
        raise ValueError(f"{where} ({key}): stock can't be negative")
    result["stock"] = stock
    result["trackStock"] = bool(raw.get("trackStock", True))

    image_url = str(raw.get("image", "")).strip()
    if image_url:
        if len(image_url) > MAX_IMAGE_URL_LEN:
            raise ValueError(f"{where} ({key}): image URL is too long")
        result["image"] = image_url

    result["isActive"] = bool(raw.get("isActive", True))
    return result


def validate_combinations(raw: Any, options: list[dict]) -> list[dict]:
    """Validate and normalise the whole combinations array against the
    already-validated `options` (the "variants" list) — a combination can
    only reference option types/values that actually exist."""
    if not isinstance(raw, list):
        raise ValueError("combinations must be a list")
    if len(raw) > MAX_COMBINATIONS:
        raise ValueError(f"too many variant combinations (max {MAX_COMBINATIONS})")
    seen_keys: set[str] = set()
    return [_validate_combination(c, i, options, seen_keys) for i, c in enumerate(raw)]


def validate_attributes(raw: dict | None) -> dict:
    """Validate a product's attributes dict. 'variants' and 'combinations'
    are structured, enforced shapes — everything else passes through
    untouched, since attributes is deliberately an open bag for anything
    else a merchant wants to store.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("attributes must be an object")
    clean = dict(raw)
    if "variants" in clean:
        clean["variants"] = validate_variants(clean["variants"])
    if "combinations" in clean:
        # combinations validates against options, so it always needs the
        # freshly-validated "variants" list from this same write — never
        # whatever combinations claims options look like, and never a
        # combinations array with no variants at all to reference.
        options = clean.get("variants") or []
        clean["combinations"] = validate_combinations(clean["combinations"], options)
    return clean
