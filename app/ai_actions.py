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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, media, products
from app.models import Category, HelpTicket, Product, Site, Tenant


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


async def create_product(db: AsyncSession, tenant_id: uuid.UUID, product: dict) -> dict:
    """Creates one product from the chat-gathered fields. Deliberately no
    `images` — the merchant adds those afterward on the normal Edit Product
    page, per the scope the user asked for. category_name is resolved to an
    id here (case-insensitive match against this site's categories) rather
    than trusting a client-supplied category_id, same reasoning as every
    other tenant-scoped write in this app.
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
        unit=product.get("unit") or None,
        free_delivery=bool(product.get("free_delivery", True)),
        delivery_charge_cents=product.get("delivery_charge_cents"),
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
# Deliberately excludes slug/sku/images/track_stock/video_url/serial_number —
# ones the merchant would only reasonably set from the actual edit page, not
# by describing them in a chat message.
_EDITABLE_PRODUCT_FIELDS = {
    "name", "price_cents", "compare_at_cents", "stock", "is_active",
    "unit", "free_delivery", "delivery_charge_cents",
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
