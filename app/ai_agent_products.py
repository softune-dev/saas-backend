"""AI agent tools for products: every field the Add Product form has.

Why this exists: the agent's product tools covered about a third of the form
(name, price, stock, descriptions, category). A merchant asking for "add 3
colors" got "I can't", because variants, SKU, cost price, video, unit,
delivery, features and per-variant stock had no tool. These two tools take the
whole form and write the same columns the dashboard saves, through the same
validators (app/products.py), so the product opens normally in the editor.

Rules kept from the REST routes (app/api/commerce.py):
  * the product plan limit applies to new products;
  * a category must be one of THIS store's categories, never guessed or created;
  * images must be this store's own uploads (ai_agent_media.own_image_url);
  * variants and combinations go through products.validate_attributes, the
    only schema JSONB has;
  * money is integer cents (CLAUDE.md rule 7).

Product descriptions are rendered as HTML on the storefront, and the text is
the model's, which may echo what a shopper typed (an order note, an inquiry).
So it is rebuilt from an allow-list of tags with no attributes (_clean_html)
instead of being stored as given.

Every change records an Undo holding the exact old values (app/ai_agent_undo.py).
"""

import html
import re
import uuid
from html.parser import HTMLParser
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_actions, ai_agent_media, cache, crud, products
from app.ai_agent_tools import _cents, _clip, _forget_tool_cache, _record, _site
from app.models import Product, Site, Tenant

UNITS = ("pcs", "kg", "g", "mg", "l", "ml")  # dashboard UNIT_OPTIONS
MAX_IMAGES = 8
MAX_FEATURES = 8
MAX_DESCRIPTION = 20_000
_URL = re.compile(r"^https://[^\s<>\"']{4,1900}$")
_ICON = re.compile(r"^[a-z0-9-]{1,40}$")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

_STR = {"type": "STRING"}
_NUM = {"type": "NUMBER"}
_BOOL = {"type": "BOOLEAN"}

_COMMON: dict[str, Any] = {
    "slug": {"type": "STRING", "description": "URL name. Leave out unless asked."},
    "sku": _STR,
    "short_description": {"type": "STRING", "description": "Up to 300 characters."},
    "description": {"type": "STRING", "description": "Full text. Plain paragraphs or simple HTML."},
    "price_taka": _NUM,
    "compare_at_price_taka": {"type": "NUMBER", "description": "Original price, shown crossed out. 0 clears it."},
    "cost_price_taka": {"type": "NUMBER", "description": "What it costs the merchant. Never shown to shoppers."},
    "stock": {"type": "INTEGER"},
    "track_stock": _BOOL,
    "category_name": {"type": "STRING", "description": "An existing category. Empty clears it."},
    "video_url": {"type": "STRING", "description": "https link to a video."},
    "serial_number": _STR,
    "unit": {"type": "STRING", "enum": list(UNITS)},
    "initial_sold_count": {"type": "INTEGER", "description": "Starting 'X sold' number."},
    "free_delivery": _BOOL,
    "delivery_locations": {
        "type": "ARRAY", "items": _STR,
        "description": "Names from the store's delivery locations this product ships to.",
    },
    "features": {"type": "ARRAY", "description": "Up to 8 icon callouts.", "items": {
        "type": "OBJECT",
        "properties": {"title": _STR, "description": _STR, "icon": {"type": "STRING", "description": "kebab-case icon, e.g. truck"}},
        "required": ["title"],
    }},
    "image_urls": {"type": "ARRAY", "items": _STR, "description": "This store's hosted image URLs; first is the cover. Replaces the gallery."},
    "variants": {"type": "ARRAY", "description": "The COMPLETE option list (Size, Color...). Replaces all options.", "items": {
        "type": "OBJECT",
        "properties": {
            "type": _STR, "is_color": _BOOL, "affects_price": _BOOL,
            "values": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "value": _STR, "hex": {"type": "STRING", "description": "#RRGGBB for colors"},
                "image_url": _STR, "price_delta_taka": _NUM,
            }, "required": ["value"]}},
        },
        "required": ["type", "values"],
    }},
    "add_variant_values": {"type": "ARRAY", "description": "Add values to an option (creating it if new) and keep the rest.", "items": {
        "type": "OBJECT",
        "properties": {
            "type": _STR, "is_color": _BOOL,
            "values": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "value": _STR, "hex": {"type": "STRING", "description": "#RRGGBB for colors"},
            }, "required": ["value"]}},
        },
        "required": ["type", "values"],
    }},
    "variant_stock": {"type": "INTEGER", "description": "Stock for each NEW option combination."},
    "combinations": {"type": "ARRAY", "description": "Set price, stock or SKU of specific option combinations.", "items": {
        "type": "OBJECT",
        "properties": {
            "options": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"type": _STR, "value": _STR}, "required": ["type", "value"]}},
            "price_taka": _NUM, "compare_at_price_taka": _NUM, "stock": {"type": "INTEGER"},
            "sku": _STR, "track_stock": _BOOL, "is_active": _BOOL,
        },
        "required": ["options"],
    }},
}

DECLARATIONS: list[dict] = [
    {
        "name": "create_product",
        "description": (
            "Create a product with any of the Add Product form's fields. Applied immediately; undo "
            "deletes it. Price is in taka. Write short_description and description yourself if the "
            "merchant gave none. Needs a name and a price."
        ),
        "parameters": {"type": "OBJECT", "properties": {"name": _STR, **_COMMON}, "required": ["name", "price_taka"]},
    },
    {
        "name": "update_product",
        "description": (
            "Edit any field of an existing product found by name, SKU or id, including variants and "
            "per-combination stock and price. Call get_product first for variants. Applied "
            "immediately; undo restores the old values."
        ),
        "parameters": {"type": "OBJECT", "properties": {
            "product_name": _STR, "product_id": _STR, "new_name": _STR, "is_active": _BOOL, **_COMMON,
        }},
    },
]


# --- description HTML ---------------------------------------------------------

class _Cleaner(HTMLParser):
    ALLOWED = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h2", "h3", "h4", "blockquote"}
    DROP = {"script", "style", "iframe", "object", "embed", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.links = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.DROP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if _URL.match(href):
                self.links += 1
                self.out.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
        elif tag in self.ALLOWED:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.DROP:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag == "a" and self.links:
            self.links -= 1
            self.out.append("</a>")
        elif tag in self.ALLOWED and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(html.escape(data))


def clean_html(text: str) -> str:
    text = str(text or "").strip()[:MAX_DESCRIPTION]
    if not _TAG.search(text):
        # Plain text collapses to one line in HTML, so keep the paragraphs.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)
    cleaner = _Cleaner()
    cleaner.feed(text)
    cleaner.close()
    return "".join(cleaner.out)


# --- variants -----------------------------------------------------------------

def _signed_cents(taka: Any) -> int:
    try:
        return int(round(float(taka) * 100))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A price must be a number") from None


def _variants_in(site: Site, raw: list) -> list[dict]:
    """Tool shape (snake_case, taka) -> the stored shape (camelCase, cents)."""
    out = []
    for option in raw:
        values = []
        for v in option.get("values") or []:
            item: dict[str, Any] = {"value": v.get("value")}
            if v.get("hex"):
                item["hex"] = v["hex"]
            if v.get("image_url"):
                item["image"] = ai_agent_media.own_image_url(site, v["image_url"])
            if v.get("price_delta_taka") is not None:
                item["priceDeltaCents"] = _signed_cents(v["price_delta_taka"])
            values.append(item)
        out.append({
            "type": option.get("type"), "isColor": bool(option.get("is_color")),
            "affectsPrice": bool(option.get("affects_price")) or any("priceDeltaCents" in v for v in values),
            "values": values,
        })
    return out


def _merge_values(current: list[dict], additions: list) -> list[dict]:
    merged = [{**o, "values": [dict(v) for v in o.get("values", [])]} for o in current]
    for add in additions:
        name = str(add.get("type") or "").strip()
        option = next((o for o in merged if o["type"].lower() == name.lower()), None)
        if option is None:
            option = {"type": name, "affectsPrice": False, "values": []}
            if add.get("is_color"):
                option["isColor"] = True
            merged.append(option)
        have = {v["value"].lower() for v in option["values"]}
        for v in add.get("values") or []:
            label = str(v.get("value") or "").strip()
            if label and label.lower() not in have:
                item: dict[str, Any] = {"value": label}
                if v.get("hex") and option.get("isColor"):
                    item["hex"] = v["hex"]
                option["values"].append(item)
                have.add(label.lower())
    return merged


def _key(pairs: dict[str, str]) -> str:
    # Same join the dashboard builds (product-variants-editor.tsx).
    return "|".join(f"{t}:{v}" for t, v in sorted(pairs.items()))


def _cartesian(options: list[dict]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [{}]
    for option in options:
        rows = [{**r, option["type"]: v["value"]} for r in rows for v in option["values"]]
    return rows


def _combinations(options, existing, overrides, default_stock) -> list[dict] | None:
    """None = leave combinations alone (a product with plain pooled stock).
    Otherwise the full list: kept rows keep their price/stock, new ones start
    from `default_stock`, and `overrides` set specific rows."""
    if not options:
        return []
    if not (existing or overrides or default_stock is not None):
        return None
    by_key = {c["key"]: c for c in existing}
    rows = []
    for pairs in _cartesian(options):
        key = _key(pairs)
        prior = by_key.get(key)
        rows.append({**prior, "optionValues": pairs} if prior else {
            "key": key, "optionValues": pairs, "stock": max(0, int(default_stock or 0)),
            "trackStock": True, "isActive": True,
        })
    index = {r["key"]: r for r in rows}
    for item in overrides:
        pairs = {str(o.get("type")): str(o.get("value")) for o in item.get("options") or []}
        row = index.get(_key(pairs))
        if row is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No such combination: {_key(pairs)}. Valid ones: " + ", ".join(index))
        for arg, field in (("price_taka", "priceCents"), ("compare_at_price_taka", "compareAtCents")):
            if item.get(arg) is not None:
                row[field] = _cents(item[arg])
        if item.get("stock") is not None:
            row["stock"] = max(0, int(item["stock"]))
        if item.get("sku") is not None:
            row["sku"] = _clip(item["sku"], products.MAX_SKU_LEN)
        for arg, field in (("track_stock", "trackStock"), ("is_active", "isActive")):
            if item.get(arg) is not None:
                row[field] = bool(item[arg])
    return rows


# --- building the column values -------------------------------------------------

async def _category(db: AsyncSession, site: Site, name: str):
    if not str(name).strip():
        return None
    found = await ai_actions._resolve_category_id(db, site, name)
    if found is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No category named “{name}”. Check the category list first.")
    return found


def _delivery(site: Site, args: dict) -> dict | None:
    locations = [l for l in (site.shipping or {}).get("locations", []) if l.get("name")]
    asked = args.get("delivery_locations")
    if asked is not None:
        by_name = {l["name"].lower(): l for l in locations}
        missing = [n for n in asked if str(n).lower() not in by_name]
        if missing:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Not a delivery location of this store: {', '.join(missing)}")
        charges = [{"name": by_name[str(n).lower()]["name"], "charge_cents": int(by_name[str(n).lower()].get("charge_cents") or 0)} for n in asked][:10]
        return {"free_delivery": not charges, "delivery_charges": charges}
    if args.get("free_delivery") is True:
        return {"free_delivery": True, "delivery_charges": []}
    if args.get("free_delivery") is False:
        if not locations:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This store has no delivery locations yet. Set them first.")
        return {"free_delivery": False, "delivery_charges": [{"name": l["name"], "charge_cents": int(l.get("charge_cents") or 0)} for l in locations][:10]}
    return None


async def _build(db: AsyncSession, site: Site, args: dict, row: Product | None) -> tuple[dict, list[str]]:
    """Validated column values for every field the caller sent, plus notes for
    the merchant. Raises HTTPException (-> {"error": ...} for the model)."""
    patch: dict[str, Any] = {}
    notes: list[str] = []
    name = args.get("new_name") or args.get("name")
    if name is not None:
        patch["name"] = _clip(name, 200)
        if not patch["name"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A product needs a name")
    if args.get("slug"):
        patch["slug"] = crud.slugify(str(args["slug"]), "product")
    for arg, col, limit in (("sku", "sku", 60), ("serial_number", "serial_number", 100), ("video_url", "video_url", 2000)):
        if args.get(arg) is not None:
            value = _clip(args[arg], limit)
            if arg == "video_url" and value and not _URL.match(value):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "video_url must be a full https:// link")
            patch[col] = value or None
    if args.get("short_description") is not None:
        patch["short_description"] = _clip(args["short_description"], 300) or None
    if args.get("description") is not None:
        patch["description"] = clean_html(args["description"]) or None
    if args.get("price_taka") is not None:
        patch["price_cents"] = _cents(args["price_taka"]) or 0
    for arg, col in (("compare_at_price_taka", "compare_at_cents"), ("cost_price_taka", "cost_price_cents")):
        if args.get(arg) is not None:
            patch[col] = _cents(args[arg]) or None  # 0 clears it
    if args.get("stock") is not None:
        patch["stock"] = max(0, int(args["stock"]))
    for arg in ("track_stock", "is_active"):
        if args.get(arg) is not None:
            patch[arg] = bool(args[arg])
    if args.get("initial_sold_count") is not None:
        patch["initial_sold_count"] = max(0, int(args["initial_sold_count"]))
    if args.get("unit") is not None:
        if args["unit"] and args["unit"] not in UNITS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unit must be one of: {', '.join(UNITS)}")
        patch["unit"] = args["unit"] or None
    if args.get("category_name") is not None:
        patch["category_id"] = await _category(db, site, args["category_name"])
    if args.get("features") is not None:
        patch["features"] = [
            {"title": _clip(f.get("title"), 60), "description": _clip(f.get("description"), 200),
             **({"icon": f["icon"]} if _ICON.match(str(f.get("icon") or "")) else {})}
            for f in args["features"] if str(f.get("title") or "").strip()
        ][:MAX_FEATURES]
    if args.get("image_urls") is not None:
        urls = [ai_agent_media.own_image_url(site, u) for u in args["image_urls"]][:MAX_IMAGES]
        patch["images"] = [{"url": u, "public_id": None} for u in urls]
    delivery = _delivery(site, args)
    if delivery:
        patch.update(delivery)

    attrs = dict(row.attributes or {}) if row else {}
    touches = any(args.get(k) is not None for k in ("variants", "add_variant_values", "combinations", "variant_stock"))
    if touches:
        options = attrs.get("variants") or []
        if args.get("variants") is not None:
            options = _variants_in(site, args["variants"])
        if args.get("add_variant_values"):
            options = _merge_values(options, args["add_variant_values"])
        try:
            options = products.validate_variants(options)
            combos = _combinations(
                options, attrs.get("combinations") or [], args.get("combinations") or [],
                args.get("variant_stock"),
            )
            attrs = {k: v for k, v in attrs.items() if k not in ("variants", "combinations")}
            if options:
                attrs["variants"] = options
            if combos:
                attrs["combinations"] = combos
            patch["attributes"] = products.validate_attributes(attrs)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        old_keys = {c["key"] for c in (row.attributes or {}).get("combinations", [])} if row else set()
        new_zero = [c for c in patch["attributes"].get("combinations", []) if c["key"] not in old_keys and c.get("stock") == 0]
        if new_zero:
            notes.append(f"{len(new_zero)} new option combinations have stock 0, so shoppers can't buy them until stock is set.")
    return patch, notes


def _summary(row: Product, patch: dict) -> dict:
    attrs = row.attributes or {}
    return {
        "id": str(row.id), "name": row.name, "slug": row.slug, "price_cents": row.price_cents, "stock": row.stock,
        "is_active": row.is_active, "category_id": str(row.category_id) if row.category_id else None,
        "changed": sorted(patch), "variants": [{"type": o["type"], "values": [v["value"] for v in o["values"]]} for o in attrs.get("variants", [])],
        "combination_count": len(attrs.get("combinations", [])),
    }


async def _unique_slug(db: AsyncSession, site: Site, base: str) -> str:
    for n in range(1, 30):
        slug = base if n == 1 else f"{base}-{n}"
        taken = (await db.execute(select(func.count()).select_from(Product).where(Product.site_id == site.id, Product.slug == slug))).scalar_one()
        if not taken:
            return slug
    return f"{base}-{uuid.uuid4().hex[:6]}"


# --- tools --------------------------------------------------------------------

async def create_product(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    if not str(args.get("name") or "").strip() or args.get("price_taka") is None:
        return {"error": "A product needs a name and a price."}
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    products.ensure_within_product_limit(await crud.count_scoped(db, Product, tenant_id), tenant.plan)
    patch, notes = await _build(db, site, args, None)
    if "free_delivery" not in patch:
        # A product charges only what it stores itself, so a new one picks up
        # the store's delivery locations or it would ship free by accident.
        charges = [{"name": l["name"], "charge_cents": int(l.get("charge_cents") or 0)}
                   for l in (site.shipping or {}).get("locations", []) if l.get("name")][:10]
        patch.update({"free_delivery": not charges, "delivery_charges": charges})
    patch["slug"] = await _unique_slug(db, site, patch.get("slug") or crud.slugify(patch["name"], "product"))
    # Not left to the column default (USD): an order takes its currency from
    # its products and sends it to the payment gateway.
    row = Product(site_id=site.id, tenant_id=site.tenant_id, currency="BDT", **patch)
    row = await crud.save(db, row)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    change = await _record(db, site, "create_product", f"Added product “{row.name}”",
                           {"type": "delete_product", "product_id": str(row.id)})
    await _forget_tool_cache(tenant_id)
    out: dict[str, Any] = {"ok": True, "product": _summary(row, patch), "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}
    out["notes"] = notes + ([] if row.images else ["No photo yet. The merchant can attach one in chat or from the product page."])
    return out


async def update_product(db: AsyncSession, tenant_id: uuid.UUID, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    row = await ai_actions._find_product(db, site, args.get("product_id"), args.get("product_name"))
    patch, notes = await _build(db, site, args, row)
    if not patch:
        return {"error": "Nothing to change on that product."}
    before = {col: getattr(row, col) for col in patch}
    if "category_id" in before:
        before["category_id"] = str(before["category_id"]) if before["category_id"] else None
    for col, value in patch.items():
        setattr(row, col, value)
    row = await crud.save(db, row)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    change = await _record(db, site, "update_product", f"Updated “{row.name}”",
                           {"type": "restore_product", "product_id": str(row.id), "before": before})
    await _forget_tool_cache(tenant_id)
    return {"ok": True, "product": _summary(row, patch), "notes": notes,
            "change": {"id": str(change.id), "summary": change.summary, "kind": "catalog"}}


HANDLERS = {"create_product": create_product, "update_product": update_product}
