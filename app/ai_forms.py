"""Field-schema registry for the generic AI action confirm form.

WHY THIS EXISTS: app/ai.py's chat_reply proposes a write as a loosely-typed
action block (see its _ACTION_BLOCK/_ACTION_TYPES) — just enough for the
dashboard to show *some* confirm card. That was fine for a one-line ticket
subject, but a merchant creating a product deserves to see (and edit) every
field the real create form would show, not a hand-built summary per action
type. This module is the single place that maps an action type to the
fields a merchant can edit, and pre-fills them with whatever the model
already inferred from the conversation.

Nothing here executes a write. app/api/ai.py calls attach_form() once, right
before handing the chat reply back to the dashboard; the actual write still
only happens when the merchant clicks Confirm and a separate
POST /ai/actions/* request lands in app/ai_actions.py — same two-step
boundary that module's docstring describes.

Shape mirrors dashboard/lib/api/ai.ts's FieldSchema type exactly — keep the
two in sync by hand, there's no shared source of truth across the
Python/TS boundary (same tradeoff app/ai.py already makes for its font
lists).
"""

import copy
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, Site


def _field(
    key: str,
    label_en: str,
    label_bn: str,
    field_type: str,
    *,
    required: bool = False,
    **extra,
) -> dict:
    field = {
        "key": key,
        "label": {"en": label_en, "bn": label_bn},
        "type": field_type,
        "required": required,
    }
    field.update(extra)
    return field


# --- set_categories: unchanged destructive full-replace, shown as one tags
# field so the merchant can still add/remove names before confirming. ---
_SET_CATEGORIES_FIELDS = [
    _field(
        "categories",
        "Categories",
        "ক্যাটাগরি",
        "tags",
        required=True,
        help={
            "en": "This replaces every existing category on your site with this list.",
            "bn": "এই তালিকা আপনার সাইটের সব বিদ্যমান ক্যাটাগরি প্রতিস্থাপন করবে।",
        },
    ),
]

# --- add_category: additive, one row via crud.save — mirrors CategoryCreate,
# where only `name` is truly required (see app/schemas.py). Image/banner/icon/
# parent_id are left out of this pass (they need a media picker / a category
# picker, not a plain field) — see the final report for that deferral. ---
_ADD_CATEGORY_FIELDS = [
    _field("name", "Category name", "ক্যাটাগরির নাম", "text", required=True, max=120),
    _field(
        "description",
        "Description",
        "বিবরণ",
        "textarea",
        max=300,
        placeholder={
            "en": "Optional — shown on the category page.",
            "bn": "ঐচ্ছিক — ক্যাটাগরি পেইজে দেখানো হবে।",
        },
    ),
    _field(
        "sort_order",
        "Sort position",
        "ক্রম",
        "number",
        min=0,
        help={"en": "Lower numbers show first.", "bn": "ছোট সংখ্যা আগে দেখানো হয়।"},
    ),
]

# --- create_product: the UI-required set is name + price (matches the real
# dashboard/product-form-page.tsx, which is stricter than ProductCreate's
# schema-required set of just `name`). The nested features/variants arrays
# stay whatever the model inferred in the conversation and are shown
# read-only in ActionConfirmCard's summary line rather than becoming
# editable repeatable-row UI in this pass. unit/track_stock/free_delivery/
# delivery_charge_cents are excluded — delivery is configured once in Site
# Settings > Shipping, not per product from chat, so the AI form doesn't
# offer it (the row still gets the normal free_delivery/track_stock column
# defaults on create). Photos: unlike every other field here, the "images"
# field type isn't something the MODEL ever fills in — Gemini has no way to
# receive file bytes — it's a real file picker the frontend renders, and
# ai-action-form.tsx uploads straight to Cloudinary via the same
# POST /sites/{id}/media?category=products route the real Edit Product page
# uses (uploadProductImage in dashboard/lib/api/commerce.ts), landing in
# `values.images` as the same {url, public_id} shape Product.images already
# expects. See _normalize_images below for the backend-side shape check. ---
_CREATE_PRODUCT_FIELDS = [
    _field(
        "images", "Photos", "ছবি", "images",
        help={
            "en": "Uploaded when you submit, same as the product edit page.",
            "bn": "সাবমিট করার সময় আপলোড হবে, প্রোডাক্ট এডিট পেইজের মতোই।",
        },
    ),
    _field("name", "Product title", "পণ্যের নাম", "text", required=True, max=200),
    _field(
        "price_cents", "Price (৳)", "মূল্য (৳)", "number",
        required=True, money=True, min=0,
    ),
    _field(
        "compare_at_cents", "Compare-at price (৳)", "তুলনামূলক মূল্য (৳)", "number",
        money=True, min=0,
        help={"en": "Shown crossed out next to the real price.", "bn": "আসল মূল্যের পাশে কাটা দেখানো হয়।"},
    ),
    _field("category_name", "Category", "ক্যাটাগরি", "select"),
    _field("sku", "SKU", "এসকেইউ", "text", max=64),
    _field("stock", "Stock", "স্টক", "number", min=0),
    _field("short_description", "Short description", "সংক্ষিপ্ত বিবরণ", "textarea", max=300),
    _field("description", "Full description", "বিস্তারিত বিবরণ", "textarea", max=20000),
]

# --- update_product deliberately has NO field-schema form. Editing an
# existing product is a text conversation, not a form-fill: the merchant
# gets the product's full current detail (app/ai_tools.py's get_product)
# posted as plain chat text, describes the change in their own words, and
# the confirm card that follows is a read-only DIFF (old → new per changed
# field) with a warning that this writes live data — never a blank/
# pre-filled form of every editable field. See _attach_product_diff below,
# which is where that diff actually gets built, and app/ai.py's system
# prompt for the full flow this backs. Images are deliberately excluded
# from this flow too — a text diff can't represent a photo, so the model is
# told to point the merchant at the real product edit page for those. ---

# --- update_order_status: mirrors OrderUpdate exactly (app/schemas.py) —
# status and notes are the ONLY editable fields on an order, everything else
# is immutable history (CLAUDE.md rule 8). order_id/order_number identify
# which order, same "context not a field" treatment as update_product's
# product_id/product_name above. ---
_UPDATE_ORDER_STATUS_FIELDS = [
    _field(
        "status", "Order status", "অর্ডারের অবস্থা", "select", required=True,
        options=[
            {"value": "pending", "label": {"en": "Pending", "bn": "পেন্ডিং"}},
            {"value": "paid", "label": {"en": "Paid", "bn": "পরিশোধিত"}},
            {"value": "fulfilled", "label": {"en": "Fulfilled", "bn": "সম্পন্ন"}},
            {"value": "cancelled", "label": {"en": "Cancelled", "bn": "বাতিল"}},
            {"value": "refunded", "label": {"en": "Refunded", "bn": "ফেরত"}},
        ],
    ),
    _field("notes", "Notes", "নোট", "textarea", max=2000),
]

# --- create_event: "coupons/events" maps entirely onto the real Event model
# (app/schemas.py's EventCreate) — there is no separate Coupon table.
# discount_percent is the one truly required field beyond name (matches
# EventCreate's own required set). product_ids is deliberately left out —
# same reasoning as create_product excluding images: binding specific
# products needs a picker, not a plain field, so an event created from chat
# starts with none bound and the merchant attaches products from the real
# Events page afterward. image_url is left out for the same reason. ---
_CREATE_EVENT_FIELDS = [
    _field("name", "Event name", "ইভেন্টের নাম", "text", required=True, max=120),
    _field(
        "discount_percent", "Discount (%)", "ছাড় (%)", "number",
        required=True, min=1, max=90,
    ),
    _field("description", "Description", "বিবরণ", "textarea", max=2000),
    _field(
        "cta_label", "Button text", "বাটনের লেখা", "text", max=40,
        placeholder={"en": "Shop now", "bn": "এখনই কিনুন"},
    ),
    _field("is_active", "Active", "সক্রিয়", "toggle"),
    _field(
        "is_popup", "Show as storefront popup", "স্টোরফ্রন্ট পপআপ হিসেবে দেখান", "toggle",
        help={
            "en": "Turns off any other event's popup on this site — only one at a time.",
            "bn": "এটি চালু করলে সাইটের অন্য পপআপ ইভেন্ট বন্ধ হয়ে যাবে — একসাথে একটিই চলতে পারে।",
        },
    ),
    _field(
        "image_only", "Image only", "শুধু ছবি", "toggle",
        help={
            "en": "Shows just the image everywhere this event appears — no title/description/button.",
            "bn": "শুধু ছবি দেখাবে — কোনো শিরোনাম/বিবরণ/বাটন থাকবে না।",
        },
    ),
]

# --- create_ticket: mirrors HelpTicketCreate (app/schemas.py) plus the real
# category buckets support triages by — see app/ai_actions.py's
# _TICKET_CATEGORIES/_TICKET_PRIORITIES docstring for why these must match
# dashboard/components/help-desk/help-data.ts exactly. ---
_TICKET_CATEGORY_OPTIONS = [
    {"value": v, "label": {"en": v, "bn": bn}}
    for v, bn in (
        ("Billing", "বিলিং"),
        ("Technical", "টেকনিক্যাল"),
        ("Domain", "ডোমেইন"),
        ("Shipping", "শিপিং"),
        ("Account", "অ্যাকাউন্ট"),
        ("Other", "অন্যান্য"),
    )
]
_TICKET_PRIORITY_OPTIONS = [
    {"value": "Low", "label": {"en": "Low", "bn": "কম"}},
    {"value": "Medium", "label": {"en": "Medium", "bn": "মাঝারি"}},
    {"value": "High", "label": {"en": "High", "bn": "জরুরি"}},
]
_CREATE_TICKET_FIELDS = [
    _field("subject", "Subject", "বিষয়", "text", required=True, max=200),
    _field(
        "category", "Category", "ক্যাটাগরি", "select", required=True,
        options=_TICKET_CATEGORY_OPTIONS,
    ),
    _field(
        "priority", "Priority", "অগ্রাধিকার", "select", required=True,
        options=_TICKET_PRIORITY_OPTIONS,
    ),
    _field("message", "Message", "বার্তা", "textarea", required=True, max=5000),
]

_SCHEMAS: dict[str, list[dict]] = {
    "set_categories": _SET_CATEGORIES_FIELDS,
    "add_category": _ADD_CATEGORY_FIELDS,
    "create_product": _CREATE_PRODUCT_FIELDS,
    "update_order_status": _UPDATE_ORDER_STATUS_FIELDS,
    "create_event": _CREATE_EVENT_FIELDS,
    "create_ticket": _CREATE_TICKET_FIELDS,
}

# --- update_product's diff labels — mirrors app/ai_actions.py's
# _EDITABLE_PRODUCT_FIELDS (kept in sync by hand, same tradeoff as
# everywhere else in this module) plus the specially-handled category_name.
# Money fields get a formatter so old/new both render as "৳1,234.00", not
# raw cents; everything else is just str(). ---
_DIFF_LABELS: dict[str, tuple[str, str]] = {
    "name": ("Product title", "পণ্যের নাম"),
    "price_cents": ("Price", "মূল্য"),
    "compare_at_cents": ("Compare-at price", "তুলনামূলক মূল্য"),
    "category_name": ("Category", "ক্যাটাগরি"),
    "stock": ("Stock", "স্টক"),
    "is_active": ("Active", "সক্রিয়"),
    "short_description": ("Short description", "সংক্ষিপ্ত বিবরণ"),
    "description": ("Full description", "বিস্তারিত বিবরণ"),
    "features": ("Feature highlights", "ফিচার হাইলাইট"),
    "variants": ("Variants", "ভ্যারিয়েন্ট"),
}
_MONEY_DIFF_KEYS = {"price_cents", "compare_at_cents"}


async def _category_options(db: AsyncSession, site_id: uuid.UUID) -> list[dict]:
    names = (
        await db.execute(
            select(Category.name).where(Category.site_id == site_id).order_by(Category.sort_order)
        )
    ).scalars().all()
    return [{"value": n, "label": {"en": n, "bn": n}} for n in names]


async def _attach_category_options(fields: list[dict], db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Shared by create_product and update_product — both have a
    category_name select field that needs this tenant's REAL category list,
    not a free-text guess. Swallows failures (see attach_form's own
    docstring): a broken dropdown must never take the whole form down with
    it, the field just renders with no options.
    """
    try:
        site = (
            await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
        ).scalars().first()
        if site is not None:
            options = await _category_options(db, site.id)
            for field in fields:
                if field["key"] == "category_name":
                    field["options"] = options
    except Exception:  # noqa: BLE001 - the form must still render without options
        pass


async def _resolve_product(
    db: AsyncSession, tenant_id: uuid.UUID, product_id: str | None, product_name: str | None
) -> Product | None:
    """Same id-or-name resolution app/ai_actions.py's _find_product uses for
    the actual write, kept independent (not imported from there) so a
    read-only diff lookup never risks raising the same HTTPException that
    function uses for a genuine write failure — this one just returns None
    and the diff degrades gracefully (see _attach_product_diff below).
    """
    try:
        site = (
            await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
        ).scalars().first()
        if site is None:
            return None

        if product_id:
            return (
                await db.execute(
                    select(Product).where(
                        Product.id == uuid.UUID(product_id), Product.site_id == site.id
                    )
                )
            ).scalars().first()

        if product_name:
            needle = product_name.strip()
            matches = (
                await db.execute(
                    select(Product).where(
                        Product.site_id == site.id,
                        or_(Product.name.ilike(f"%{needle}%"), Product.sku.ilike(f"%{needle}%")),
                    )
                )
            ).scalars().all()
            return matches[0] if len(matches) == 1 else None

        return None
    except Exception:  # noqa: BLE001 - best-effort only, never blocks the diff
        return None


def _diff_display(key: str, value: object) -> str:
    if value is None:
        return "—"
    if key in _MONEY_DIFF_KEYS:
        try:
            return f"৳{int(value) / 100:,.2f}"
        except (TypeError, ValueError):
            return str(value)
    if key == "is_active":
        return "Yes" if value else "No"
    if key in ("features", "variants") and isinstance(value, list):
        return f"{len(value)} item(s)"
    return str(value)


async def _attach_product_diff(action: dict, db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Builds the read-only old->new diff update_product's confirm card
    shows instead of a form (see this module's update_product comment
    above). Resolves the CURRENT row so "old" is real data, not a guess —
    without that, a diff would only ever be able to show the new value,
    which isn't a diff at all, just the same summary the old bespoke card
    already gave. Best-effort: if the product can't be resolved (deleted
    mid-conversation, ambiguous name), the diff still renders with old
    values as "—" rather than losing the confirm card entirely.
    """
    product = action.get("product") or {}
    product_id = product.get("product_id")
    product_name = product.get("product_name")
    row = await _resolve_product(db, tenant_id, product_id, product_name)

    old_category_name = None
    if row is not None and row.category_id is not None:
        category = (
            await db.execute(select(Category).where(Category.id == row.category_id))
        ).scalars().first()
        old_category_name = category.name if category else None

    old_values: dict = {}
    if row is not None:
        old_values = {
            "name": row.name,
            "price_cents": row.price_cents,
            "compare_at_cents": row.compare_at_cents,
            "category_name": old_category_name,
            "stock": row.stock,
            "is_active": row.is_active,
            "short_description": row.short_description,
            "description": row.description,
            "features": row.features,
            "variants": (row.attributes or {}).get("variants"),
        }

    diff = []
    for key, new_value in product.items():
        if key in ("product_id", "product_name") or key not in _DIFF_LABELS:
            continue
        label_en, label_bn = _DIFF_LABELS[key]
        diff.append(
            {
                "key": key,
                "label": {"en": label_en, "bn": label_bn},
                "old": _diff_display(key, old_values.get(key)),
                "new": _diff_display(key, new_value),
            }
        )

    return {
        **action,
        "diff": diff,
        "product_context": {
            "product_id": product_id,
            "product_name": row.name if row is not None else product_name,
        },
    }


async def attach_form(action: dict, db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Returns `action` with `fields` (the schema to render) and `values`
    (current, pre-filled values) attached — or `action` unchanged for a type
    with no registered schema (create_ticket keeps its bespoke
    ActionConfirmCard presentation).

    update_product is handled separately (_attach_product_diff) — it gets a
    `diff` instead of `fields`/`values`, deliberately never the generic form
    (see this module's update_product comment above).

    Best-effort only: a failure building the (optional) category dropdown
    must never break the whole chat reply, so it's swallowed and the field
    just renders with no options rather than the merchant losing the form.
    """
    action_type = action.get("type")
    if action_type == "update_product":
        return await _attach_product_diff(action, db, tenant_id)

    schema = _SCHEMAS.get(action_type)
    if schema is None:
        return action

    fields = copy.deepcopy(schema)
    values: dict = {}

    if action_type == "set_categories":
        values["categories"] = action.get("categories") or []

    elif action_type == "add_category":
        values["name"] = action.get("name") or ""
        values["description"] = action.get("description") or ""
        values["sort_order"] = action.get("sort_order")

    elif action_type == "create_product":
        product = action.get("product") or {}
        for field in fields:
            if field["key"] in product:
                values[field["key"]] = product[field["key"]]
        await _attach_category_options(fields, db, tenant_id)

    elif action_type == "update_order_status":
        # order_id/order_number are the same "identify, don't edit" case as
        # product_id/product_name above.
        values["order_id"] = action.get("order_id")
        values["order_number"] = action.get("order_number")
        values["status"] = action.get("status") or ""
        values["notes"] = action.get("notes") or ""

    elif action_type == "create_event":
        values["name"] = action.get("name") or ""
        values["description"] = action.get("description") or ""
        values["discount_percent"] = action.get("discount_percent")
        values["cta_label"] = action.get("cta_label") or "Shop now"
        values["is_active"] = bool(action.get("is_active", False))
        values["is_popup"] = bool(action.get("is_popup", False))
        values["image_only"] = bool(action.get("image_only", False))

    elif action_type == "create_ticket":
        values["subject"] = action.get("subject") or ""
        values["category"] = action.get("category") or ""
        values["priority"] = action.get("priority") or "Medium"
        values["message"] = action.get("message") or ""

    return {**action, "fields": fields, "values": values}
