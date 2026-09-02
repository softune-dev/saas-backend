"""PUBLIC endpoints — what the customer-facing sites on Vercel actually call.

No authentication: these serve published content to anonymous visitors.
Heavily cached: this is the highest-traffic path in the system.

This is the file that makes the whole "edit in admin panel, no redeploy"
architecture work. A Next.js template calls /public/site/{host} at render time
(server-side, so search engines see real HTML); a Vite template calls it from the
browser on load.

Resolved SEO is computed HERE, not in the template. Doing it server-side once
means every template — Next.js or Vite, now or in two years — gets identical,
correct metadata without reimplementing the fallback rules.
"""

import json
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bkash, cache, courier_crypto, crud, mailer, nagad, queue, recaptcha, sslcommerz
from app.ratelimit import _client_ip, demo_access_rate_limit, rate_limit
from app.config import settings
from app.db import get_db
from app.models import (
    Category,
    DemoAccessRequest,
    FraudBlocklistEntry,
    Inquiry,
    Order,
    OrderItem,
    PageView,
    PaymentConnection,
    Product,
    Site,
    SitePage,
    User,
)
from app.schemas import (
    DemoAccessIn,
    InquiryCreate,
    InquiryOut,
    PageViewIn,
    PlatformContactIn,
    PublicOrderCreate,
    PublicOrderItemOut,
    PublicOrderOut,
    TokenOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])
DB = Annotated[AsyncSession, Depends(get_db)]


async def _find_published_site(host: str, db: AsyncSession) -> Site:
    bare = host.split(".")[0]
    site = (
        await db.execute(
            select(Site).where(
                or_(Site.custom_domain == host, Site.subdomain == bare),
                Site.status == "published",
            )
        )
    ).scalar_one_or_none()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found or not published")
    return site


def _resolve_seo(site: Site, page: SitePage) -> dict[str, Any]:
    """Merge page SEO over site SEO. Page wins; site fills the gaps.

    Every template gets the same answer, so a customer who set a site-wide OG
    image once does not have to set it again on all nine pages.
    """
    site_seo = site.seo or {}
    page_seo = page.seo or {}
    business = site.business or {}

    title = page_seo.get("title") or page.title
    suffix = site_seo.get("title_suffix") or site.name
    # Don't produce "Home | Acme" when the page title IS "Acme".
    full_title = title if suffix in title else f"{title} | {suffix}"

    description = (
        page_seo.get("meta_description")
        or site_seo.get("meta_description")
        or business.get("description")
        or ""
    )[:160]

    return {
        "title": full_title[:70],  # Google truncates past ~60-70 chars
        "description": description,
        "keywords": site_seo.get("keywords") or "",
        "og_title": page_seo.get("og_title") or site_seo.get("og_title") or full_title[:70],
        "og_description": page_seo.get("og_description") or site_seo.get("og_description") or description,
        "og_image": page_seo.get("og_image") or site_seo.get("og_image") or "",
        "favicon": site_seo.get("favicon") or "",
        # A draft site sets noindex at site level; a single page can also opt out.
        "noindex": bool(site_seo.get("noindex") or page_seo.get("noindex")),
        "canonical": _canonical(site, page.slug),
    }


def _canonical(site: Site, slug: str) -> str:
    """One authoritative URL per page.

    Necessary because a site can be reachable at BOTH acme.yourdomain.com and
    acme.com once a custom domain is attached. Without a canonical tag, Google
    sees two copies of everything and splits the ranking between them.
    """
    host = site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}"
    path = f"/{slug}".rstrip("/")
    return f"https://{host}{path or '/'}"


def _json_ld(site: Site) -> dict[str, Any] | None:
    """Structured data, generated from business details the customer already gave.

    This is the payoff of storing business details once on the site: the customer
    fills in their address and phone during setup, and gets rich-result eligible
    markup for free. Never ask them to type it twice.
    """
    b = site.business or {}
    if not b.get("name"):
        return None

    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": b.get("type") or "LocalBusiness",
        "name": b["name"],
        "url": _canonical(site, ""),
    }
    if b.get("description"):
        data["description"] = b["description"]
    if b.get("phone"):
        data["telephone"] = b["phone"]
    if b.get("email"):
        data["email"] = b["email"]
    if b.get("logo_url"):
        data["logo"] = b["logo_url"]
    if addr := b.get("address"):
        data["address"] = {
            "@type": "PostalAddress",
            "streetAddress": addr.get("street", ""),
            "addressLocality": addr.get("city", ""),
            "addressRegion": addr.get("region", ""),
            "postalCode": addr.get("postal_code", ""),
            "addressCountry": addr.get("country", ""),
        }
    if hours := b.get("opening_hours"):
        data["openingHours"] = hours
    socials = b.get("socials")
    if isinstance(socials, dict):
        # sameAs is how Google links a site to its social profiles.
        data["sameAs"] = [u for u in socials.values() if u]
    elif isinstance(socials, list):
        data["sameAs"] = [u for u in socials if u]
    return data


@router.get("/site/{host}")
async def get_site_config(host: str, db: DB) -> dict:
    """Everything a template needs to render a site. Cached in Redis.

    `host` may be a full hostname (acme.vercel.app, acme.com) or the bare
    subdomain (acme) — templates differ in what they can see of the request, so
    accept both rather than making each one normalise.
    """
    key = cache.site_key(host)
    if cached := await cache.get_json(key):
        return cached

    # 404, not 403, is raised inside — an unpublished site should be
    # indistinguishable from one that does not exist. Otherwise you leak which
    # subdomains are taken.
    site = await _find_published_site(host, db)

    pages = (
        await db.execute(
            select(SitePage)
            .where(SitePage.site_id == site.id, SitePage.is_published)
            .order_by(SitePage.sort_order, SitePage.slug)
        )
    ).scalars().all()

    # Only status='connected' methods reach checkout — a merchant who
    # disconnects bKash (or never finished setting up Manual) shouldn't have
    # it silently keep showing as an option. Only non-secret fields: never
    # api_key_hint/encrypted columns, and gateways (bkash/nagad/etc.) don't
    # have a live checkout flow yet regardless of connection status — see
    # app/api/payments.py's module docstring.
    payment_connections = (
        await db.execute(
            select(PaymentConnection).where(
                PaymentConnection.site_id == site.id,
                PaymentConnection.status == "connected",
            )
        )
    ).scalars().all()
    payment_methods = [
        {
            "provider": pc.provider,
            "label": pc.label or pc.provider,
            "config": pc.config,
        }
        for pc in payment_connections
    ]

    config = {
        "site": {
            "id": str(site.id),
            "name": site.name,
            "template_key": site.template.key,
            "framework": site.template.framework,
            "theme": site.theme,
            "business": site.business,
            "about": site.about,
            # Raw site-wide SEO — separate from each page's already-resolved
            # `seo` block below. Templates use this for things that apply
            # once per site rather than per page: tracking script ids,
            # keywords, and the sitemap on/off toggle.
            "seo": {
                "keywords": site.seo.get("keywords", ""),
                "sitemap_enabled": site.seo.get("sitemap_enabled", True),
                "google_analytics": site.seo.get("google_analytics", ""),
                "google_search_console": site.seo.get("google_search_console", ""),
                "facebook_pixel": site.seo.get("facebook_pixel", ""),
                "tiktok_pixel": site.seo.get("tiktok_pixel", ""),
                "gtm_container_id": site.seo.get("gtm_container_id", ""),
                "favicon": site.seo.get("favicon", ""),
            },
            "faqs": site.faqs,
            "legal": site.legal,
            "payment_methods": payment_methods,
        },
        # Prebuilt nav so templates don't each write their own menu logic.
        "nav": [
            {"title": p.title, "path": f"/{p.slug}".rstrip("/") or "/"} for p in pages
        ],
        "pages": [
            {
                "slug": p.slug,
                "path": f"/{p.slug}".rstrip("/") or "/",
                "title": p.title,
                "blocks": p.blocks,
                "seo": _resolve_seo(site, p),
            }
            for p in pages
        ],
        "json_ld": _json_ld(site),
        "updated_at": site.updated_at.isoformat(),
    }

    await cache.set_json(key, config)
    return config


def _public_category(category: Category, item_count: int) -> dict[str, Any]:
    """Shape a Category row for template consumption.

    Only fields that actually exist in the `categories` table are returned.
    Templates (see Aurora's ProductCategory type) must treat anything else —
    e.g. a "featured" flag — as absent rather than assume a value; we do not
    invent data the customer never entered.
    """
    return {
        "id": str(category.id),
        "slug": category.slug,
        "name": category.name,
        "description": category.description or "",
        "image": category.image_url or "",
        "banner": category.banner_url or "",
        "icon": category.icon or "",
        "itemCount": item_count,
    }


def _public_product(product: Product, category_name: str | None) -> dict[str, Any]:
    """Shape a Product row for template consumption.

    Same rule as `_public_category`: only real columns. `price_cents` is the
    source of truth (see CLAUDE.md rule 7 — money is integer cents); this is
    the one place it's converted to a decimal major-unit number for display,
    because templates render prices, they don't do currency math.
    """
    return {
        "id": str(product.id),
        "slug": product.slug,
        "name": product.name,
        "description": product.description or "",
        # The merchant's own short blurb — separate from `description` (rich
        # HTML) since a storefront needs a real plain-text excerpt, not one
        # derived by stripping tags off the long version.
        "shortDescription": product.short_description or "",
        "price": product.price_cents / 100,
        "compareAtPrice": (
            product.compare_at_cents / 100 if product.compare_at_cents is not None else None
        ),
        "currency": product.currency,
        "images": [img.get("url", "") for img in (product.images or []) if img.get("url")],
        "video": product.video_url or "",
        "categoryId": str(product.category_id) if product.category_id else None,
        "categoryName": category_name,
        "inStock": (not product.track_stock) or product.stock > 0,
        "stockCount": product.stock,
        "attributes": product.attributes or {},
        "features": product.features or [],
        "freeDelivery": product.free_delivery,
        # Real merchant-set options only — an empty list here means "the
        # merchant hasn't configured delivery pricing for this product",
        # not "delivery is free". Templates must treat that honestly (see
        # CartContext.tsx) rather than inventing a flat fallback charge.
        "deliveryCharges": [
            {"name": dc.get("name", ""), "charge": (dc.get("charge_cents") or 0) / 100}
            for dc in (product.delivery_charges or [])
            if dc.get("name")
        ],
    }


@router.get("/site/{host}/categories")
async def list_public_categories(host: str, db: DB) -> list[dict]:
    """Active categories for a published site, each with its active product count."""
    site = await _find_published_site(host, db)

    categories = (
        await db.execute(
            select(Category)
            .where(Category.site_id == site.id, Category.is_active)
            .order_by(Category.sort_order, Category.name)
        )
    ).scalars().all()

    counts = dict(
        (
            await db.execute(
                select(Product.category_id, func.count(Product.id))
                .where(Product.site_id == site.id, Product.is_active)
                .group_by(Product.category_id)
            )
        ).all()
    )

    return [_public_category(c, counts.get(c.id, 0)) for c in categories]


@router.get("/site/{host}/products")
async def list_public_products(
    host: str,
    db: DB,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Active products for a published site, optionally filtered by category slug."""
    site = await _find_published_site(host, db)

    filters = [Product.site_id == site.id, Product.is_active]
    if category:
        cat = (
            await db.execute(
                select(Category).where(Category.site_id == site.id, Category.slug == category)
            )
        ).scalar_one_or_none()
        if cat is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        filters.append(Product.category_id == cat.id)

    total = (
        await db.execute(select(func.count(Product.id)).where(*filters))
    ).scalar_one()
    rows = (
        await db.execute(
            select(Product, Category.name)
            .outerjoin(Category, Product.category_id == Category.id)
            .where(*filters)
            .order_by(Product.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return {
        "items": [_public_product(p, cat_name) for p, cat_name in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/site/{host}/products/{slug}")
async def get_public_product(host: str, slug: str, db: DB) -> dict:
    """One active product by slug — what a product detail page renders."""
    site = await _find_published_site(host, db)

    row = (
        await db.execute(
            select(Product, Category.name)
            .outerjoin(Category, Product.category_id == Category.id)
            .where(Product.site_id == site.id, Product.slug == slug, Product.is_active)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    product, category_name = row
    return _public_product(product, category_name)


@router.post(
    "/contact",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("platform-contact", limit=5, window_seconds=600))],
)
async def submit_platform_contact(payload: PlatformContactIn, request: Request) -> None:
    """The landing site's own "Contact Us" — platform-level, no site/host
    involved at all (unlike submit_contact_form below, which is a
    merchant's storefront contact form and requires one). Previously had NO
    backend at all — the landing form just opened a mailto: link. Queued,
    not awaited inline — same reasoning as _queue_otp_email in
    app/api/leads.py: a real SMTP send takes several seconds, which
    shouldn't sit in front of the visitor's "Message sent" confirmation.
    """
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "platform_contact", _client_ip(request), payload.recaptcha_v2_token
        )
    )
    subject, html_body, text_body = mailer.contact_email(
        f"{payload.first_name} {payload.last_name}", payload.email, payload.phone, payload.message
    )
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": settings.smtp_from_email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )


@router.post(
    "/site/{host}/contact",
    response_model=InquiryOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("contact", limit=5, window_seconds=600))],
)
async def submit_contact_form(
    host: str, payload: InquiryCreate, request: Request, db: DB
) -> Inquiry:
    """What a site's ContactForm block submits to. No auth — anonymous visitors
    use this. The published-site check is the only gate: a draft or deleted
    site's form must not silently accept and store submissions nobody will read.

    Rate-limited to 5 submissions per IP per 10 minutes — see app/ratelimit.py.
    Also reCAPTCHA-gated — see app/recaptcha.py.
    """
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "contact", _client_ip(request), payload.recaptcha_v2_token
        )
    )
    site = await _find_published_site(host, db)
    inquiry = Inquiry(tenant_id=site.tenant_id, site_id=site.id, data=payload.data)
    return await crud.save(db, inquiry)


@router.post(
    "/site/{host}/pageview",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("pageview", limit=120, window_seconds=300))],
)
async def log_page_view(host: str, request: Request, db: DB) -> None:
    """Fired by the storefront's own PageViewBeacon on every route change —
    real visitor/traffic data for app/api/analytics.py (see PageView's
    docstring for why session_id isn't a real identity).

    No reCAPTCHA (see PageViewIn's docstring) — the generous rate limit
    (120/5min/IP, well above genuine browsing) is the abuse guard. Best-
    effort: any failure here is swallowed, never surfaced to the visitor —
    a broken analytics beacon must never be the reason a page looks broken.

    Body is parsed manually rather than declared as a `PageViewIn` param:
    FastAPI's automatic body parsing only auto-decodes JSON when the
    Content-Type header is exactly "application/json" — but the client
    sends this via navigator.sendBeacon, which MUST use "text/plain" (a
    CORS-safelisted content type) for a cross-origin call, since sendBeacon
    can't complete the CORS preflight "application/json" would force (see
    lib/pageview.ts in each template). Parsing the raw body ourselves works
    regardless of whatever Content-Type the browser actually sent.
    """
    try:
        raw = await request.body()
        payload = PageViewIn.model_validate_json(raw)
        site = await _find_published_site(host, db)
        db.add(
            PageView(
                tenant_id=site.tenant_id,
                site_id=site.id,
                path=payload.path,
                referrer=payload.referrer,
                session_id=payload.session_id,
            )
        )
        await db.commit()
    except HTTPException:
        # Draft/deleted site — a real 404 from _find_published_site, not
        # worth logging as a warning, just as silent as any other failure.
        pass
    except Exception:
        log.warning("pageview: failed to record view for host %s", host, exc_info=True)


# Phone normalize/extract helpers live in crud.py now — shared with
# get_or_create_customer, which also needs to collapse phone formatting
# variants to the same key. See crud.normalize_phone / crud.extract_customer_phone.


# All seven BD mobile operator prefixes — 013 Grameenphone, 014 Banglalink,
# 015 Teletalk, 016 Airtel/Robi, 017 Grameenphone, 018 Robi, 019 Banglalink.
# Checkout only accepts a real BD mobile number (no email option, no OTP
# gate anywhere), so this is the one shape check standing between an
# anonymous request and the merchant's order list.
_BD_MOBILE_RE = re.compile(r"^01[3-9]\d{8}$")


def _validate_bd_phone(raw: str) -> bool:
    """Accepts 01XXXXXXXXX, 8801XXXXXXXXX, or +8801XXXXXXXXX (with
    optional spaces/dashes) — anything else is rejected."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("880"):
        digits = "0" + digits[3:]  # 8801XXXXXXXXX -> 01XXXXXXXXX
    return bool(_BD_MOBILE_RE.match(digits))


@router.post(
    "/site/{host}/orders",
    response_model=PublicOrderOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("checkout", limit=8, window_seconds=300))],
)
async def create_public_order(
    host: str, payload: PublicOrderCreate, request: Request, db: DB
) -> PublicOrderOut:
    """What a real storefront checkout submits. No auth — anonymous
    customers use this.

    PRICES AND DELIVERY FEE COME FROM THE DATABASE, NEVER FROM THE REQUEST —
    same rule as commerce.py's create_order, extended to shipping_cents too,
    since here the caller is an anonymous browser, not an authenticated
    merchant. The delivery fee is recomputed here exactly the way
    CartContext.tsx computes it for display (sum of each item's matching
    delivery_charges entry for the chosen location, 0 for an item that
    doesn't support it) — never trusted from the client.

    Rate-limited to 8 orders per IP per 5 minutes — see app/ratelimit.py.
    Loose enough for a real shopper retrying a failed payment, tight enough
    to stop a script placing hundreds of fake orders with no OTP gate.
    Also reCAPTCHA-gated — see app/recaptcha.py.
    """
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "checkout", _client_ip(request), payload.recaptcha_v2_token
        )
    )
    site = await _find_published_site(host, db)

    # Phone validation — see _validate_bd_phone. Required, not optional: with
    # no OTP/SMS verification anywhere in checkout, an unvalidated phone
    # field is a free-for-all for fake orders and garbage data reaching the
    # merchant's real order list.
    phone_for_validation = crud.extract_customer_phone(payload.customer)
    if not _validate_bd_phone(phone_for_validation):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Please enter a valid Bangladeshi mobile number (e.g. 017XXXXXXXX).",
        )

    # Fraud blocklist enforcement — see Settings → Fraud Protection. Rule is
    # opt-out (defaults to enabled — see fraud-data.ts's defaultRuleState),
    # checked against the CURRENT order's phone only, no history needed.
    block_rule = (site.fraud_rules or {}).get("block_blocklist") or {}
    if block_rule.get("enabled", True):
        phone = phone_for_validation
        normalized = crud.normalize_phone(phone)
        if normalized:
            blocklisted = (
                await db.execute(
                    select(FraudBlocklistEntry).where(FraudBlocklistEntry.site_id == site.id)
                )
            ).scalars().all()
            match = next(
                (e for e in blocklisted if crud.normalize_phone(e.phone) == normalized), None
            )
            if match:
                # Queued, not awaited inline — see queue.JOB_SEND_ORDER_NOTIFICATIONS'
                # docstring. publish() is a single fire-and-forget AMQP call
                # (already swallows its own failures), so this doesn't add a
                # DB round trip to the rejection response either.
                await queue.publish(
                    queue.JOB_SEND_ORDER_NOTIFICATIONS,
                    {
                        "tenant_id": str(site.tenant_id),
                        "site_id": str(site.id),
                        "type": "order_blocked",
                        "title": "Blocked order attempt",
                        "body": f"{phone} tried to place an order — this number is on your blocklist"
                        + (f" ({match.note})" if match.note else "") + ".",
                        "link": "/settings/fraud",
                        "send_push": False,
                    },
                )
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "We couldn't process this order. Please contact us directly to complete your purchase.",
                )

    ids = [item.product_id for item in payload.items]
    products = {
        p.id: p
        for p in (
            await db.execute(
                select(Product).where(
                    Product.id.in_(ids),
                    Product.site_id == site.id,
                    Product.is_active,
                )
            )
        ).scalars()
    }
    missing = [str(i) for i in ids if i not in products]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Products not found or unavailable: {', '.join(missing)}",
        )

    items: list[OrderItem] = []
    out_items: list[PublicOrderItemOut] = []
    subtotal = 0
    shipping = 0
    for line in payload.items:
        product = products[line.product_id]

        if product.track_stock and product.stock < line.quantity:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Only {product.stock} left of '{product.name}'.",
            )

        line_total = product.price_cents * line.quantity
        subtotal += line_total

        if not product.free_delivery and payload.delivery_location:
            match = next(
                (
                    dc
                    for dc in (product.delivery_charges or [])
                    if dc.get("name") == payload.delivery_location
                ),
                None,
            )
            if match:
                shipping += (match.get("charge_cents") or 0) * line.quantity

        items.append(
            OrderItem(
                product_id=product.id,
                name_snapshot=product.name,
                sku_snapshot=product.sku,
                unit_price_cents=product.price_cents,
                cost_price_cents_snapshot=product.cost_price_cents,
                quantity=line.quantity,
                total_cents=line_total,
            )
        )
        out_items.append(
            PublicOrderItemOut(
                name=product.name,
                quantity=line.quantity,
                unit_price_cents=product.price_cents,
                total_cents=line_total,
            )
        )
        if product.track_stock:
            product.stock -= line.quantity

    customer_record = await crud.get_or_create_customer(
        db, tenant_id=site.tenant_id, site_id=site.id, customer=payload.customer
    )

    order = Order(
        site_id=site.id,
        tenant_id=site.tenant_id,
        order_number=await crud.next_order_number(db, site.id),
        customer=payload.customer,
        customer_id=customer_record.id if customer_record else None,
        subtotal_cents=subtotal,
        shipping_cents=shipping,
        tax_cents=0,
        total_cents=subtotal + shipping,
        currency=next(iter(products.values())).currency,
        notes=payload.notes,
        meta={
            "payment_method": payload.payment_method,
            "delivery_location": payload.delivery_location,
            "transaction_id": payload.transaction_id,
        },
        items=items,
    )
    order = await crud.save(db, order)
    # A real storefront checkout — the merchant's dashboard (analytics,
    # orders, products' stock counts, customers) must not show stale numbers
    # after this. See app/cache.py's module docstring for why this drops the
    # whole site's dashboard cache rather than picking specific keys.
    await cache.invalidate_dashboard(str(site.id))

    customer_name = " ".join(
        str(payload.customer.get(k, "")).strip()
        for k in ("first_name", "last_name")
    ).strip() or "A customer"
    order_title = f"New order {order.order_number}"
    order_body = f"{customer_name} placed an order for {order.currency} {(subtotal + shipping) / 100:.2f}."
    # Queued, not awaited inline: the bell notification is its own DB commit,
    # and push is a real network call to Chrome/Firefox/etc's push service
    # per subscribed browser (100-500ms+, easily). Doing either before
    # answering the customer's checkout request made every order slower —
    # see queue.JOB_SEND_ORDER_NOTIFICATIONS and worker.py's handler, which
    # now does this off the request path entirely.
    await queue.publish(
        queue.JOB_SEND_ORDER_NOTIFICATIONS,
        {
            "tenant_id": str(site.tenant_id),
            "site_id": str(site.id),
            "type": "order_created",
            "title": order_title,
            "body": order_body,
            "link": f"/orders?highlight={order.id}",
            "send_push": True,
        },
    )

    # Server-side Meta Purchase event — see app/marketing.py and
    # worker.py's handle_send_meta_capi_event. `event_id` is the order_number
    # (not the internal uuid — PublicOrderOut never exposes that) so the
    # storefront's own client-side pixel can fire the same Purchase event
    # with the same eventID and have Meta deduplicate the two into a single
    # conversion instead of double-counting it. Queued unconditionally: the
    # handler itself checks whether the site actually has a pixel ID +
    # connected CAPI token before doing any network call.
    await queue.publish(
        queue.JOB_SEND_META_CAPI_EVENT,
        {
            "site_id": str(site.id),
            "event_id": order.order_number,
            "event_time": int(order.created_at.timestamp()),
            "value": (subtotal + shipping) / 100,
            "currency": order.currency,
            "order_number": order.order_number,
            "customer_phone": crud.extract_customer_phone(payload.customer) or None,
            "customer_email": payload.customer.get("email")
            if isinstance(payload.customer.get("email"), str)
            else None,
            "client_ip": _client_ip(request),
            "user_agent": request.headers.get("user-agent"),
            "event_source_url": f"https://{host}/checkout",
        },
    )

    return PublicOrderOut(
        order_number=order.order_number,
        items=out_items,
        subtotal_cents=subtotal,
        shipping_cents=shipping,
        total_cents=subtotal + shipping,
        currency=order.currency,
        delivery_location=payload.delivery_location,
        created_at=order.created_at,
    )


# =============================================================================
#  Gateway checkout — bKash / SSLCommerz / Nagad
# =============================================================================
# The order already exists (status="pending", created by create_public_order
# above, same as a COD/manual order) by the time any of this runs — these
# endpoints only ever ADVANCE that order to "paid" (or leave it "pending"/
# mark "cancelled" on failure), never create one. Keyed by order_number, not
# the internal id — PublicOrderOut never exposes that (see its own
# docstring), and order_number is exactly what the storefront already has.
#
# Real proof of payment is always a server-to-server call THIS backend makes
# (validate_transaction / execute_payment / verify_payment) — never trusting
# a redirect or webhook body on its own, which is trivially spoofable by
# anyone who can guess or intercept the callback URL.


async def _find_order_by_number(host: str, order_number: str, db: AsyncSession) -> tuple[Site, Order]:
    site = await _find_published_site(host, db)
    order = (
        await db.execute(
            select(Order).where(Order.site_id == site.id, Order.order_number == order_number)
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return site, order


async def _connected_gateway(site_id, provider: str, db: AsyncSession) -> PaymentConnection | None:
    return (
        await db.execute(
            select(PaymentConnection).where(
                PaymentConnection.site_id == site_id,
                PaymentConnection.provider == provider,
                PaymentConnection.status == "connected",
            )
        )
    ).scalar_one_or_none()


def _storefront_redirect(host: str, order_number: str, ok: bool) -> str:
    outcome = "success" if ok else "failed"
    return f"https://{host}/checkout/{outcome}?order={order_number}"


async def _finalize_gateway_payment(
    db: AsyncSession, site: Site, order: Order, provider: str,
    ok: bool, transaction_ref: str | None, raw: dict | None,
) -> None:
    """Idempotent: SSLCommerz can hit this twice for the same order (IPN
    AND the browser redirect both land here) — once the order is off
    "pending", every later call is a no-op, not a double-charge/refund."""
    if order.status != "pending":
        return
    order.status = "paid" if ok else "cancelled"
    order.meta = {
        **order.meta,
        f"{provider}_transaction_id": transaction_ref,
        f"{provider}_confirmed_status": (raw or {}).get("status"),
    }
    await crud.save(db, order)
    await cache.invalidate_dashboard(str(site.id))
    if ok:
        await queue.publish(
            queue.JOB_SEND_ORDER_NOTIFICATIONS,
            {
                "tenant_id": str(site.tenant_id),
                "site_id": str(site.id),
                "type": "order_created",
                "title": f"Payment confirmed — {order.order_number}",
                "body": f"{provider.capitalize()} payment of {order.currency} "
                f"{order.total_cents / 100:.2f} confirmed.",
                "link": f"/orders?highlight={order.id}",
                "send_push": True,
            },
        )


@router.post(
    "/site/{host}/orders/{order_number}/pay/{provider}",
    dependencies=[Depends(rate_limit("gateway-init", limit=10, window_seconds=300))],
)
async def init_gateway_payment(
    host: str, order_number: str, provider: str, request: Request, db: DB
) -> dict:
    """Starts a real checkout session and returns the URL to send the
    customer's browser to. Called right after create_public_order, when the
    customer picked bkash/sslcommerz/nagad instead of cod/manual."""
    if provider not in ("sslcommerz", "bkash", "nagad"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown or unsupported gateway '{provider}'")

    site, order = await _find_order_by_number(host, order_number, db)
    if order.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order has already been paid or cancelled.")

    conn = await _connected_gateway(site.id, provider, db)
    if conn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{provider} isn't set up for this store.")

    sandbox = bool((conn.config or {}).get("sandbox", True))
    callback_url = f"{settings.api_base_url}/public/site/{host}/orders/{order_number}/pay/{provider}/callback"
    amount = order.total_cents / 100
    customer = order.customer or {}
    customer_name = " ".join(
        str(customer.get(k, "")).strip() for k in ("first_name", "last_name")
    ).strip() or "Customer"
    customer_phone = crud.extract_customer_phone(customer) or ""
    customer_email = customer.get("email") if isinstance(customer.get("email"), str) else ""
    redirect_url: str | None = None
    error: str | None = None

    if provider == "sslcommerz":
        store_id = courier_crypto.decrypt(conn.api_key_encrypted)
        store_passwd = courier_crypto.decrypt(conn.secret_key_encrypted)
        redirect_url, error = await sslcommerz.create_session(
            store_id, store_passwd, sandbox=sandbox,
            tran_id=order_number, amount=amount, currency=order.currency,
            success_url=callback_url, fail_url=callback_url,
            cancel_url=callback_url, ipn_url=callback_url,
            customer_name=customer_name, customer_email=customer_email,
            customer_phone=customer_phone,
            customer_address=customer.get("address") if isinstance(customer.get("address"), str) else "",
            product_name=f"Order {order_number}",
        )
    elif provider == "bkash":
        app_key = courier_crypto.decrypt(conn.api_key_encrypted)
        app_secret = courier_crypto.decrypt(conn.secret_key_encrypted)
        extra = json.loads(courier_crypto.decrypt(conn.extra_encrypted or ""))
        token, token_error = await bkash.grant_token(
            app_key, app_secret, extra["username"], extra["password"], sandbox
        )
        if token is None:
            error = token_error
        else:
            redirect_url, payment_id, error = await bkash.create_payment(
                app_key, token, sandbox=sandbox, amount=amount, currency=order.currency,
                merchant_invoice_number=order_number, callback_url=callback_url,
            )
            if payment_id:
                order.meta = {**order.meta, "bkash_payment_id": payment_id}
                await crud.save(db, order)
    else:  # nagad
        merchant_id = (conn.config or {}).get("merchant_id", "")
        merchant_private_key = courier_crypto.decrypt(conn.secret_key_encrypted)
        extra = json.loads(courier_crypto.decrypt(conn.extra_encrypted or ""))
        redirect_url, error = await nagad.create_payment(
            merchant_id, merchant_private_key, extra["nagad_public_key"], sandbox=sandbox,
            client_ip=_client_ip(request), order_id=order_number, amount=amount,
            callback_url=callback_url,
        )

    if redirect_url is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, error or f"{provider} couldn't start this checkout.")
    return {"redirect_url": redirect_url}


@router.api_route("/site/{host}/orders/{order_number}/pay/sslcommerz/callback", methods=["GET", "POST"])
async def sslcommerz_callback(host: str, order_number: str, request: Request, db: DB):
    """Handles BOTH SSLCommerz's IPN (server-to-server POST — the real
    confirmation) and the customer's browser being redirected here after
    paying (GET). Either way, val_id is only trusted after
    validate_transaction confirms it server-to-server."""
    site, order = await _find_order_by_number(host, order_number, db)

    if request.method == "POST":
        form = await request.form()
        val_id = form.get("val_id")
    else:
        val_id = request.query_params.get("val_id")

    ok = False
    if val_id:
        conn = await _connected_gateway(site.id, "sslcommerz", db)
        if conn:
            store_id = courier_crypto.decrypt(conn.api_key_encrypted)
            store_passwd = courier_crypto.decrypt(conn.secret_key_encrypted)
            sandbox = bool((conn.config or {}).get("sandbox", True))
            ok, data, _error = await sslcommerz.validate_transaction(
                store_id, store_passwd, sandbox=sandbox, val_id=str(val_id)
            )
            await _finalize_gateway_payment(db, site, order, "sslcommerz", ok, str(val_id), data)

    if request.method == "POST":
        return {"status": "ok"}
    return RedirectResponse(_storefront_redirect(host, order_number, ok))


@router.get("/site/{host}/orders/{order_number}/pay/bkash/callback")
async def bkash_callback(host: str, order_number: str, request: Request, db: DB) -> RedirectResponse:
    site, order = await _find_order_by_number(host, order_number, db)

    payment_id = request.query_params.get("paymentID")
    status_param = request.query_params.get("status")
    ok = False
    if payment_id and status_param == "success":
        conn = await _connected_gateway(site.id, "bkash", db)
        if conn:
            app_key = courier_crypto.decrypt(conn.api_key_encrypted)
            app_secret = courier_crypto.decrypt(conn.secret_key_encrypted)
            extra = json.loads(courier_crypto.decrypt(conn.extra_encrypted or ""))
            sandbox = bool((conn.config or {}).get("sandbox", True))
            token, _token_error = await bkash.grant_token(
                app_key, app_secret, extra["username"], extra["password"], sandbox
            )
            if token:
                ok, data, _error = await bkash.execute_payment(
                    app_key, token, sandbox=sandbox, payment_id=payment_id
                )
                await _finalize_gateway_payment(db, site, order, "bkash", ok, payment_id, data)

    return RedirectResponse(_storefront_redirect(host, order_number, ok))


@router.get("/site/{host}/orders/{order_number}/pay/nagad/callback")
async def nagad_callback(host: str, order_number: str, request: Request, db: DB) -> RedirectResponse:
    site, order = await _find_order_by_number(host, order_number, db)

    payment_ref_id = request.query_params.get("payment_ref_id")
    ok = False
    if payment_ref_id:
        conn = await _connected_gateway(site.id, "nagad", db)
        if conn:
            sandbox = bool((conn.config or {}).get("sandbox", True))
            ok, data, _error = await nagad.verify_payment(payment_ref_id, sandbox=sandbox)
            await _finalize_gateway_payment(db, site, order, "nagad", ok, payment_ref_id, data)

    return RedirectResponse(_storefront_redirect(host, order_number, ok))


@router.post(
    "/demo-access",
    response_model=TokenOut,
    dependencies=[Depends(demo_access_rate_limit)],
)
async def demo_access(payload: DemoAccessIn, request: Request, db: DB) -> dict:
    """Mints a real token pair for the shared plan="demo" account — no
    signup, no password, no lead-funnel staging. Still requires a real
    email first: previously this handed out working tokens for free with
    nothing recorded, no way to follow up with anyone who tried it. One
    row per email in demo_access_requests (an outreach list, not a click
    log — see migrations/050). No IP limit and no error on a repeat email —
    the demo is meant to have zero friction, it just upserts the same row.
    Only cap is 5/day per email (demo_access_rate_limit), to stop one
    address being scripted, not to gate real visitors.

    Read-only regardless of who's holding the token afterward, enforced by
    block_demo_writes (app/security.py) — deliberately decoupled from trial
    signup (app/api/trial.py), which is the "actually use it" path."""
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "demo_access", _client_ip(request), payload.recaptcha_v2_token
        )
    )

    demo_user = (
        await db.execute(select(User).where(User.email == settings.demo_user_email))
    ).scalar_one_or_none()
    if demo_user is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Demo isn't available right now — please contact us instead.",
        )

    existing = (
        await db.execute(select(DemoAccessRequest).where(DemoAccessRequest.email == payload.email))
    ).scalar_one_or_none()
    ip = _client_ip(request)
    if existing:
        existing.request_count += 1
        existing.last_requested_at = func.now()
        existing.ip = ip
        await crud.save(db, existing)
    else:
        await crud.save(db, DemoAccessRequest(email=payload.email, ip=ip))

    from app.api.auth import _tokens  # local import: avoids a circular import at module load

    return _tokens(demo_user).model_dump()


@router.get("/site/{host}/sitemap.xml")
async def sitemap(host: str, db: DB) -> dict:
    """Sitemap data, generated from the page list.

    Returned as JSON, not XML: each template renders its own /sitemap.xml route
    from this, because the URLs must be on the customer's domain, not ours.
    Auto-generated so a customer never has to think about sitemaps at all.
    """
    config = await get_site_config(host, db)
    if not config["site"]["seo"].get("sitemap_enabled", True):
        return {"urls": []}
    return {
        "urls": [
            {
                "loc": page["seo"]["canonical"],
                "lastmod": config["updated_at"],
                "priority": 1.0 if page["path"] == "/" else 0.7,
            }
            for page in config["pages"]
            if not page["seo"]["noindex"]
        ]
    }
