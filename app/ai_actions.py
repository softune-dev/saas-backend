"""Write actions the chat assistant can PROPOSE — never execute on its own.

Everything in app/ai_tools.py is read-only and auto-executes because a
lookup can't hurt anything. These actions actually change data (replacing a
site's categories, creating a product, editing one that already exists), so
the flow is deliberately two-step: app/ai.py's chat_reply asks Gemini to
describe what it WOULD do as a small JSON block, the dashboard renders that
as a card with Confirm/Cancel, and only a real button click here — a
separate HTTP request, authenticated and tenant-scoped exactly like any
other write in this app — actually touches the database. The model never
gets a code path straight to a write.

SINGLE-SITE ASSUMPTION: like app/ai_tools.py's business-overview tool, this
resolves "the tenant's site" as the first Site row for that tenant_id, not a
site_id the model provides. Fine while every tenant has one site; would need
a real site_id once that's no longer true.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, events, media, products
from app.models import Category, Event, HelpTicket, Order, Product, Site, Tenant


async def _resolve_site(db: AsyncSession, tenant_id: uuid.UUID) -> Site:
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id).limit(1))
    ).scalars().first()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No site found for this account")
    return site


async def _resolve_category_id(
    db: AsyncSession, site: Site, category_name: str | None
) -> uuid.UUID | None:
    name = (category_name or "").strip()
    if not name:
        return None
    cat = (
        await db.execute(
            select(Category).where(Category.site_id == site.id, Category.name.ilike(name))
        )
    ).scalars().first()
    return cat.id if cat else None


async def _find_product(
    db: AsyncSession, site: Site, product_id: str | None, product_name: str | None
) -> Product:
    """Locates an existing product to edit — by id when the model has one
    (from a prior list_products tool call), else by a case-insensitive name
    match scoped to this site. Ambiguous or missing matches raise a clear
    error rather than silently picking one, since editing the wrong product
    is a worse outcome than the chat asking the merchant to be specific.
    """
    if product_id:
        try:
            pid = uuid.UUID(product_id)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid product id")
        product = (
            await db.execute(
                select(Product).where(Product.id == pid, Product.site_id == site.id)
            )
        ).scalars().first()
        if product is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
        return product

    name = (product_name or "").strip()
    if not name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Need a product id or name to edit"
        )
    matches = (
        await db.execute(
            select(Product).where(Product.site_id == site.id, Product.name.ilike(f"%{name}%"))
        )
    ).scalars().all()
    if len(matches) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f'No product matching "{name}"')
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches[:5])
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'"{name}" matches multiple products ({names}) — ask which one to edit.',
        )
    return matches[0]


async def _find_order(
    db: AsyncSession, site: Site, order_id: str | None, order_number: str | None
) -> Order:
    """Locates an existing order to update — by id when the model has a real
    UUID, else by an EXACT order_number match scoped to this site. Unlike
    _find_product's fuzzy ilike name search, an order number is a precise
    identifier the merchant already has in hand (it's printed on the order
    itself, and list_orders/get_order both return it) — a fuzzy match here
    would risk updating the wrong order's status, a worse outcome than
    "the order needs to be created before its status can be a name."
    """
    if order_id:
        try:
            oid = uuid.UUID(order_id)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid order id")
        order = (
            await db.execute(select(Order).where(Order.id == oid, Order.site_id == site.id))
        ).scalars().first()
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        return order

    number = (order_number or "").strip()
    if not number:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Need an order id or order number to update"
        )
    order = (
        await db.execute(
            select(Order).where(Order.site_id == site.id, Order.order_number == number)
        )
    ).scalars().first()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f'No order matching "{number}"')
    return order


async def set_categories(
    db: AsyncSession, tenant_id: uuid.UUID, category_names: list[str]
) -> list[dict]:
    """Replaces every category on the tenant's site with a fresh list built
    from `category_names`. Deletion sets products' category_id to NULL (the
    FK is ON DELETE SET NULL, same as commerce.py's delete_category) rather
    than deleting products — losing a category assignment is recoverable,
    losing a product is not.
    """
    site = await _resolve_site(db, tenant_id)

    existing = (
        await db.execute(select(Category).where(Category.site_id == site.id))
    ).scalars().all()
    for cat in existing:
        image_url = cat.image_url
        banner_url = cat.banner_url
        await crud.delete(db, cat)
        if image_url:
            media.delete_by_url(image_url, site.subdomain)
        if banner_url:
            media.delete_by_url(banner_url, site.subdomain)

    created: list[Category] = []
    for i, name in enumerate(category_names):
        name = name.strip()
        if not name:
            continue
        category = Category(
            site_id=site.id,
            tenant_id=site.tenant_id,
            name=name,
            slug=crud.slugify(name, "category"),
            sort_order=i,
        )
        created.append(await crud.save(db, category))

    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return [{"id": str(c.id), "name": c.name, "slug": c.slug} for c in created]


async def add_category(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    description: str | None = None,
    sort_order: int | None = None,
) -> dict:
    """Adds ONE category without touching any existing ones — the additive
    counterpart to set_categories' destructive full-replace. Mirrors
    CategoryCreate's real required set (only `name`); slug is auto-derived
    same as the manual "Add category" form does.
    """
    site = await _resolve_site(db, tenant_id)

    name = (name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Category needs a name")

    if sort_order is None:
        # Append after whatever already exists, same default a merchant
        # adding one by hand from the Categories page would get.
        sort_order = (
            await db.execute(
                select(func.count()).select_from(Category).where(Category.site_id == site.id)
            )
        ).scalar_one()

    category = Category(
        site_id=site.id,
        tenant_id=site.tenant_id,
        name=name,
        slug=crud.slugify(name, "category"),
        description=(description or "").strip() or None,
        sort_order=sort_order,
    )
    category = await crud.save(db, category)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return {"id": str(category.id), "name": category.name, "slug": category.slug}


def _normalize_images(images: object) -> list[dict]:
    """Images arrive already-uploaded — ai-action-form.tsx's "images" field
    uploads straight to Cloudinary via the real media endpoint the moment a
    file is picked (see app/ai_forms.py's create_product field comment) and
    only ever hands this module back {url, public_id} dicts, never raw file
    bytes (Gemini has no way to receive those). This just guards the shape
    before it reaches Product.images, same defensiveness as every other
    JSONB write in this module — a malformed dict here would otherwise
    reach a customer's live storefront.
    """
    if not isinstance(images, list):
        return []
    return [
        {"url": img["url"], "public_id": img.get("public_id")}
        for img in images
        if isinstance(img, dict) and isinstance(img.get("url"), str) and img["url"].strip()
    ]


async def create_product(db: AsyncSession, tenant_id: uuid.UUID, product: dict) -> dict:
    """Creates one product from the chat-gathered fields. `images` is never
    something the MODEL fills in — it can't receive file bytes — it's
    whatever the merchant uploaded directly in the confirm form's Photos
    field before clicking Confirm (see _normalize_images above).
    category_name is resolved to an id here (case-insensitive match against
    this site's categories) rather than trusting a client-supplied
    category_id, same reasoning as every other tenant-scoped write in this
    app.
    """
    site = await _resolve_site(db, tenant_id)

    # Same plan cap the manual "Add product" endpoint enforces — this path
    # writes a Product row directly (crud.save), not through
    # POST /sites/{site_id}/products, so it needs its own check or the AI
    # assistant would be a silent bypass of the limit.
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    existing_count = await crud.count_scoped(db, Product, tenant_id)
    products.ensure_within_product_limit(existing_count, tenant.plan)

    category_id = await _resolve_category_id(db, site, product.get("category_name"))

    attributes: dict = {}
    variants = product.get("variants")
    if variants:
        try:
            attributes["variants"] = products.validate_variants(variants)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    name = str(product.get("name", "")).strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Product needs a name")

    price_cents = int(product.get("price_cents") or 0)
    features = [
        {"title": str(f.get("title", ""))[:60], "description": str(f.get("description", ""))[:200]}
        for f in (product.get("features") or [])
        if str(f.get("title", "")).strip()
    ][:8]

    row = Product(
        site_id=site.id,
        tenant_id=site.tenant_id,
        name=name,
        slug=crud.slugify(name, "product"),
        description=product.get("description") or None,
        short_description=product.get("short_description") or None,
        price_cents=price_cents,
        category_id=category_id,
        images=_normalize_images(product.get("images")),
        attributes=attributes,
        features=features,
    )
    row = await crud.save(db, row)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "price_cents": row.price_cents,
        "category_id": str(row.category_id) if row.category_id else None,
    }


# Fields the chat assistant is allowed to change on an existing product.
# Deliberately excludes slug/sku/track_stock/video_url/serial_number — ones
# the merchant would only reasonably set from the actual edit page, not by
# describing them in a chat message — and unit/free_delivery/
# delivery_charge_cents, which are configured once in Site Settings >
# Shipping, not per product. `images` is handled separately below (needs
# _normalize_images, not a plain setattr) rather than added here.
_EDITABLE_PRODUCT_FIELDS = {
    "name", "price_cents", "compare_at_cents", "stock", "is_active",
    "short_description", "description",
}


async def update_product(db: AsyncSession, tenant_id: uuid.UUID, product: dict) -> dict:
    """Edits fields on a product that already exists — the piece that was
    missing: the assistant could only create, never touch what it (or the
    merchant) already made. Only fields actually present in `product` are
    changed; everything else on the row is left alone, same partial-update
    semantics as ProductUpdate/commerce.py's own PATCH route.
    """
    site = await _resolve_site(db, tenant_id)
    row = await _find_product(db, site, product.get("product_id"), product.get("product_name"))

    if "category_name" in product:
        row.category_id = await _resolve_category_id(db, site, product.get("category_name"))

    if "variants" in product:
        try:
            attrs = dict(row.attributes or {})
            attrs["variants"] = products.validate_variants(product["variants"])
            row.attributes = attrs
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    if "features" in product:
        row.features = [
            {"title": str(f.get("title", ""))[:60], "description": str(f.get("description", ""))[:200]}
            for f in (product.get("features") or [])
            if str(f.get("title", "")).strip()
        ][:8]

    if "images" in product:
        row.images = _normalize_images(product["images"])

    for field in _EDITABLE_PRODUCT_FIELDS:
        if field in product:
            setattr(row, field, product[field])

    row = await crud.save(db, row)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "price_cents": row.price_cents,
        "stock": row.stock,
        "is_active": row.is_active,
        "category_id": str(row.category_id) if row.category_id else None,
    }


# Mirrors OrderUpdate's own validator (app/schemas.py) exactly — status is
# the only field with a fixed vocabulary; notes is free text. Totals/line
# items are NOT in this set on purpose (CLAUDE.md rule 8: order history is
# immutable, and migrations/003_commerce.sql explains why a total must never
# be recomputed after the fact).
_ORDER_STATUSES = {"pending", "paid", "fulfilled", "cancelled", "refunded"}


async def update_order_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    order_id: str | None = None,
    order_number: str | None = None,
    new_status: str | None = None,
    notes: str | None = None,
) -> dict:
    """Edits ONLY status/notes on an existing order — the two fields
    OrderUpdate allows. (Parameter is `new_status`, not `status`, purely to
    avoid shadowing the `status` module imported at the top of this file for
    HTTPException codes; the confirm endpoint's JSON field is still plain
    "status", matching OrderUpdate.) No cache.invalidate_site — order status
    doesn't render on the live storefront the way a product/category edit
    does, only the dashboard needs to know, same as commerce.py's own
    update_order route.
    """
    site = await _resolve_site(db, tenant_id)
    row = await _find_order(db, site, order_id, order_number)

    if new_status is not None:
        if new_status not in _ORDER_STATUSES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"status must be one of: {', '.join(sorted(_ORDER_STATUSES))}",
            )
        row.status = new_status
    if notes is not None:
        row.notes = notes

    row = await crud.save(db, row)
    await cache.invalidate_dashboard(str(site.id))
    return {
        "id": str(row.id),
        "order_number": row.order_number,
        "status": row.status,
        "notes": row.notes,
    }


async def create_event(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    description: str | None = None,
    discount_percent: int | None = None,
    cta_label: str = "Shop now",
    is_active: bool = False,
    is_popup: bool = False,
    image_only: bool = False,
) -> dict:
    """Creates one promo/coupon Event — "coupons/events" maps entirely onto
    this model, there's no separate Coupon table (see app/ai_forms.py's
    create_event schema comment). Deliberately no product_ids: binding
    specific products needs a name/id picker the chat form doesn't have this
    pass, same reasoning as create_product leaving out images — the merchant
    attaches products from the real Events page afterward.
    """
    site = await _resolve_site(db, tenant_id)

    # Same per-plan cap the manual "Create event" endpoint enforces
    # (app/api/events.py) — this path writes an Event row directly via
    # crud.save, not through that endpoint, so it needs its own check.
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    existing_count = await crud.count_scoped(db, Event, tenant_id)
    events.ensure_within_event_limit(existing_count, tenant.plan)

    name = (name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Event needs a name")
    if discount_percent is None or not (1 <= int(discount_percent) <= 90):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Discount percent must be between 1 and 90"
        )

    if is_popup:
        # Same DB-level backstop as app/api/events.py's _clear_other_popups —
        # at most one popup event per site (migrations/062's partial unique
        # index). Cleared here first so turning one event's popup on doesn't
        # 409 against whichever event already had it.
        await db.execute(
            update(Event).where(Event.site_id == site.id, Event.is_popup).values(is_popup=False)
        )

    event = Event(
        site_id=site.id,
        tenant_id=site.tenant_id,
        name=name,
        slug=crud.slugify(name, "event"),
        description=(description or "").strip() or None,
        cta_label=(cta_label or "Shop now").strip()[:40] or "Shop now",
        discount_percent=int(discount_percent),
        is_active=bool(is_active),
        is_popup=bool(is_popup),
        image_only=bool(image_only),
    )
    event = await crud.save(db, event)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    return {
        "id": str(event.id),
        "name": event.name,
        "slug": event.slug,
        "discount_percent": event.discount_percent,
        "is_active": event.is_active,
        "is_popup": event.is_popup,
    }


# Must match dashboard/components/help-desk/help-data.ts's ticketCategories
# exactly — this is the same dropdown a merchant filling out the form by
# hand would see, so the assistant's auto-picked category should land in
# one of the same real buckets support actually triages by, not an
# invented one that never shows up anywhere else.
_TICKET_CATEGORIES = {"Billing", "Technical", "Domain", "Shipping", "Account", "Other"}
_TICKET_PRIORITIES = {"Low", "Medium", "High"}


async def create_ticket(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    subject: str,
    category: str,
    priority: str,
    message: str,
) -> dict:
    """Files a real Help Desk ticket (same table/endpoint as the merchant
    filling out the form themselves — see app/api/help_desk.py) on the
    assistant's behalf, once the merchant has confirmed the exact
    subject/category/priority/message shown in the chat's confirm card.
    Never auto-submitted from inside chat_reply itself — this only runs from
    the separate confirm endpoint, same two-step boundary as every other
    action in this module.
    """
    subject = subject.strip()[:200]
    message = message.strip()[:5000]
    if not subject or not message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ticket needs a subject and message")

    category = category if category in _TICKET_CATEGORIES else "Other"
    priority = priority if priority in _TICKET_PRIORITIES else "Medium"

    ticket = HelpTicket(
        tenant_id=tenant_id,
        user_id=user_id,
        subject=subject,
        category=category,
        priority=priority,
        message=message,
    )
    ticket = await crud.save(db, ticket)
    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
    }
