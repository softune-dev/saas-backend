"""Categories, products and orders — the CRUD core.

All routes are nested under /sites/{site_id}/ because a catalogue belongs to a
storefront, not to the whole account. Every route resolves the site through
crud.get_scoped first, so ownership is proven before anything else happens.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, media, products
from app.db import get_db
from app.models import Category, Inquiry, Order, OrderItem, Product, Site
from app.schemas import (
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    InquiryOut,
    OrderCreate,
    OrderOut,
    OrderUpdate,
    Page,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)
from app.security import CurrentUser

router = APIRouter(tags=["commerce"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _owned_site(db: AsyncSession, tenant_id: uuid.UUID, site_id: uuid.UUID) -> Site:
    return await crud.get_scoped(db, Site, tenant_id, site_id)


def _product_image_urls(product: Product) -> set[str]:
    return {img["url"] for img in (product.images or []) if isinstance(img, dict) and img.get("url")}


# =============================================================================
#  Categories
# =============================================================================


@router.get("/sites/{site_id}/categories", response_model=list[CategoryOut])
async def list_categories(
    site_id: uuid.UUID, user: CurrentUser, db: DB
) -> list[Category]:
    await _owned_site(db, user.tenant_id, site_id)
    stmt = (
        select(Category)
        .where(Category.site_id == site_id)
        .order_by(Category.sort_order, Category.name)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/sites/{site_id}/categories",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    site_id: uuid.UUID, payload: CategoryCreate, user: CurrentUser, db: DB
) -> Category:
    site = await _owned_site(db, user.tenant_id, site_id)

    if payload.parent_id:
        # A parent from another site (or another tenant) would silently build a
        # cross-site tree. Verify it belongs here before accepting it.
        parent = await crud.get_scoped(db, Category, user.tenant_id, payload.parent_id)
        if parent.site_id != site_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Parent is on another site")

    data = payload.model_dump()
    data["slug"] = payload.slug or crud.slugify(payload.name, "category")
    # The dashboard never sends an explicit sort_order — every category was
    # landing with sort_order=0 (CategoryCreate's default), so the public
    # listing's `ORDER BY sort_order, name` fell through to alphabetical on
    # every tie, silently discarding the order a merchant added categories
    # in. Append new ones after whatever already exists for this site so
    # creation order is preserved unless the merchant later reorders them.
    if data["sort_order"] == 0:
        current_max = await db.execute(
            select(func.max(Category.sort_order)).where(Category.site_id == site.id)
        )
        data["sort_order"] = (current_max.scalar() or 0) + 1
    category = Category(site_id=site.id, tenant_id=site.tenant_id, **data)
    return await crud.save(db, category)


@router.patch("/sites/{site_id}/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    site_id: uuid.UUID,
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    user: CurrentUser,
    db: DB,
) -> Category:
    site = await _owned_site(db, user.tenant_id, site_id)
    category = await crud.get_scoped(db, Category, user.tenant_id, category_id)

    if payload.parent_id == category_id:
        # A category that is its own parent creates an infinite loop the moment
        # anything tries to walk the tree.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A category cannot be its own parent")

    old_image_url = category.image_url
    old_banner_url = category.banner_url
    crud.apply_updates(category, payload.model_dump(exclude_unset=True))
    category = await crud.save(db, category)

    if old_image_url and old_image_url != category.image_url:
        media.delete_by_url(old_image_url, site.subdomain)
    if old_banner_url and old_banner_url != category.banner_url:
        media.delete_by_url(old_banner_url, site.subdomain)
    return category


@router.delete(
    "/sites/{site_id}/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_category(
    site_id: uuid.UUID, category_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    category = await crud.get_scoped(db, Category, user.tenant_id, category_id)
    image_url = category.image_url
    banner_url = category.banner_url
    # Products in it are NOT deleted — the FK is ON DELETE SET NULL. They become
    # uncategorised, which is recoverable. Deleting them would not be.
    await crud.delete(db, category)
    if image_url:
        media.delete_by_url(image_url, site.subdomain)
    if banner_url:
        media.delete_by_url(banner_url, site.subdomain)


# =============================================================================
#  Products
# =============================================================================


@router.get("/sites/{site_id}/products", response_model=Page[ProductOut])
async def list_products(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    q: Annotated[str | None, Query(description="Search product names")] = None,
    category_id: uuid.UUID | None = None,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)

    filters = [Product.site_id == site_id]
    if q:
        # ilike hits the GIN trigram index from migrations/003 — see the comment
        # there for why that index is what makes this fast instead of a scan.
        filters.append(Product.name.ilike(f"%{q}%"))
    if category_id:
        filters.append(Product.category_id == category_id)
    if active_only:
        filters.append(Product.is_active)

    rows, total = await crud.list_scoped(
        db, Product, user.tenant_id, filters=filters,
        order_by=Product.created_at.desc(), limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/sites/{site_id}/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    site_id: uuid.UUID, payload: ProductCreate, user: CurrentUser, db: DB
) -> Product:
    site = await _owned_site(db, user.tenant_id, site_id)

    if payload.category_id:
        category = await crud.get_scoped(db, Category, user.tenant_id, payload.category_id)
        if category.site_id != site_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Category is on another site")

    data = payload.model_dump()
    data["slug"] = payload.slug or crud.slugify(payload.name, "product")
    data["currency"] = data["currency"].upper()
    try:
        data["attributes"] = products.validate_attributes(data.get("attributes"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    product = Product(site_id=site.id, tenant_id=site.tenant_id, **data)
    product = await crud.save(db, product)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    return product


@router.get("/sites/{site_id}/products/{product_id}", response_model=ProductOut)
async def get_product(
    site_id: uuid.UUID, product_id: uuid.UUID, user: CurrentUser, db: DB
) -> Product:
    await _owned_site(db, user.tenant_id, site_id)
    return await crud.get_scoped(db, Product, user.tenant_id, product_id)


@router.patch("/sites/{site_id}/products/{product_id}", response_model=ProductOut)
async def update_product(
    site_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ProductUpdate,
    user: CurrentUser,
    db: DB,
) -> Product:
    site = await _owned_site(db, user.tenant_id, site_id)
    product = await crud.get_scoped(db, Product, user.tenant_id, product_id)

    data = payload.model_dump(exclude_unset=True)
    if "attributes" in data:
        try:
            data["attributes"] = products.validate_attributes(data["attributes"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    old_image_urls = _product_image_urls(product)
    crud.apply_updates(product, data)
    product = await crud.save(db, product)
    await cache.invalidate_site(site.subdomain, site.custom_domain)

    # Same reasoning as sites.py's theme diff: the gallery editor uploads
    # each new image immediately, but only stops referencing a removed one
    # the moment this save actually lands — that's when it's safe to delete.
    if "images" in data:
        for url in old_image_urls - _product_image_urls(product):
            media.delete_by_url(url, site.subdomain)
    return product


@router.delete(
    "/sites/{site_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product(
    site_id: uuid.UUID, product_id: uuid.UUID, user: CurrentUser, db: DB
) -> None:
    site = await _owned_site(db, user.tenant_id, site_id)
    product = await crud.get_scoped(db, Product, user.tenant_id, product_id)
    image_urls = _product_image_urls(product)
    # Past order_items survive: their FK is SET NULL and they carry snapshots of
    # name/sku/price. See migrations/003_commerce.sql.
    await crud.delete(db, product)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    for url in image_urls:
        media.delete_by_url(url, site.subdomain)


# =============================================================================
#  Orders
# =============================================================================


@router.get("/sites/{site_id}/orders", response_model=Page[OrderOut])
async def list_orders(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    order_status: Annotated[str | None, Query(alias="status")] = None,
    # 500, not the usual 100 — the dashboard's Sales Analysis widget buckets
    # raw orders into 6 calendar months client-side (see dashboard-view.tsx),
    # and 100 orders covers well under 6 months for any store with real
    # volume, silently leaving older months blank even though the data
    # exists. 500 covers 6 months at ~2.7 orders/day before this needs a
    # real server-side monthly-aggregation endpoint instead.
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)

    filters = [Order.site_id == site_id]
    if order_status:
        filters.append(Order.status == order_status)

    rows, total = await crud.list_scoped(
        db, Order, user.tenant_id, filters=filters,
        order_by=Order.created_at.desc(), limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/sites/{site_id}/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED
)
async def create_order(
    site_id: uuid.UUID, payload: OrderCreate, user: CurrentUser, db: DB
) -> Order:
    """Create an order from a list of product ids and quantities.

    PRICES COME FROM THE DATABASE, NEVER FROM THE REQUEST. The client sends only
    product_id and quantity. If it could send a price, anyone could buy a laptop
    for one cent by editing the request — the single most common e-commerce
    vulnerability there is.
    """
    site = await _owned_site(db, user.tenant_id, site_id)

    # Fetch every product in ONE query rather than looping. For a 20-item cart
    # that is 1 round-trip instead of 20 — and the tenant/site filter is applied
    # in the same statement, so a product from another store cannot sneak in.
    ids = [item.product_id for item in payload.items]
    products = {
        p.id: p
        for p in (
            await db.execute(
                select(Product).where(
                    Product.id.in_(ids),
                    Product.site_id == site_id,
                    Product.tenant_id == user.tenant_id,
                )
            )
        ).scalars()
    }

    missing = [str(i) for i in ids if i not in products]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Products not found on this site: {', '.join(missing)}",
        )

    items: list[OrderItem] = []
    subtotal = 0
    for line in payload.items:
        product = products[line.product_id]

        if not product.is_active:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"'{product.name}' is not available."
            )
        if product.track_stock and product.stock < line.quantity:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Only {product.stock} left of '{product.name}'.",
            )

        line_total = product.price_cents * line.quantity
        subtotal += line_total
        items.append(
            OrderItem(
                product_id=product.id,
                # Snapshots: what the buyer actually saw and agreed to pay.
                name_snapshot=product.name,
                sku_snapshot=product.sku,
                unit_price_cents=product.price_cents,
                quantity=line.quantity,
                total_cents=line_total,
            )
        )
        if product.track_stock:
            product.stock -= line.quantity

    order = Order(
        site_id=site.id,
        tenant_id=site.tenant_id,
        order_number=await crud.next_order_number(db, site.id),
        customer=payload.customer,
        subtotal_cents=subtotal,
        shipping_cents=payload.shipping_cents,
        tax_cents=payload.tax_cents,
        total_cents=subtotal + payload.shipping_cents + payload.tax_cents,
        currency=next(iter(products.values())).currency,
        notes=payload.notes,
        meta=payload.meta,
        items=items,
    )
    # ONE commit for the order, its items, AND the stock decrements. If any part
    # fails, all of it rolls back — you never get an order with no lines, or
    # stock deducted for an order that was never created.
    return await crud.save(db, order)


@router.get("/sites/{site_id}/orders/{order_id}", response_model=OrderOut)
async def get_order(
    site_id: uuid.UUID, order_id: uuid.UUID, user: CurrentUser, db: DB
) -> Order:
    await _owned_site(db, user.tenant_id, site_id)
    return await crud.get_scoped(db, Order, user.tenant_id, order_id)


@router.patch("/sites/{site_id}/orders/{order_id}", response_model=OrderOut)
async def update_order(
    site_id: uuid.UUID,
    order_id: uuid.UUID,
    payload: OrderUpdate,
    user: CurrentUser,
    db: DB,
) -> Order:
    """Only status and notes are editable — totals are immutable history."""
    await _owned_site(db, user.tenant_id, site_id)
    order = await crud.get_scoped(db, Order, user.tenant_id, order_id)
    crud.apply_updates(order, payload.model_dump(exclude_unset=True))
    return await crud.save(db, order)


# =============================================================================
#  Inquiries — contact form submissions (the site owner's inbox)
# =============================================================================


@router.get("/sites/{site_id}/inquiries", response_model=Page[InquiryOut])
async def list_inquiries(
    site_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    await _owned_site(db, user.tenant_id, site_id)
    rows, total = await crud.list_scoped(
        db, Inquiry, user.tenant_id,
        filters=[Inquiry.site_id == site_id],
        order_by=Inquiry.created_at.desc(), limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.patch("/sites/{site_id}/inquiries/{inquiry_id}", response_model=InquiryOut)
async def mark_inquiry(
    site_id: uuid.UUID,
    inquiry_id: uuid.UUID,
    inquiry_status: Annotated[str, Query(alias="status")],
    user: CurrentUser,
    db: DB,
) -> Inquiry:
    """Mark read/archived. A query param, not a body, since it's the only field
    that ever changes — a full Update schema would be one field for no benefit."""
    if inquiry_status not in ("new", "read", "archived"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid status")
    await _owned_site(db, user.tenant_id, site_id)
    inquiry = await crud.get_scoped(db, Inquiry, user.tenant_id, inquiry_id)
    inquiry.status = inquiry_status
    return await crud.save(db, inquiry)
