"""Background worker — consumes RabbitMQ jobs.

Run it in a SECOND terminal, alongside the API:

    venv\\Scripts\\activate
    python -m app.worker

Then watch http://localhost:15672 (guest/guest) to see messages arrive and drain.

WHY A SEPARATE PROCESS: it can be restarted, scaled or crash without touching
the API. A slow email send or an unreachable Vercel never makes a user wait.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import aio_pika
import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Category, Invoice, MarketingConnection, Notification, Order, Product, Site, Tenant, User
from app.queue import (
    JOB_ATTACH_DOMAIN,
    JOB_CAPTURE_SCREENSHOT,
    JOB_DETACH_DOMAIN,
    JOB_GENERATE_INVOICE_PDF,
    JOB_GENERATE_SITEMAP,
    JOB_REVALIDATE_SITE,
    JOB_SEND_EMAIL,
    JOB_SEND_META_CAPI_EVENT,
    JOB_SEND_LOW_STOCK_EMAIL,
    JOB_SEND_ORDER_EMAIL,
    JOB_SEND_ORDER_NOTIFICATIONS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s worker | %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Handlers
# ---------------------------------------------------------------------------


async def handle_revalidate_site(payload: dict) -> None:
    """Tell a live Next.js site to refresh its cached pages.

    THIS IS THE KEY TO 'EDIT WITHOUT REDEPLOY'. The site's pages are statically
    cached on Vercel for speed. This call invalidates just the changed paths, so
    the next visitor gets fresh content — seconds, not a 2-minute rebuild.

    Vite sites need no call: they fetch config in the browser on every load, so
    they are never stale. We skip them rather than fire a pointless request.
    """
    site_id = payload.get("site_id")
    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()

    if site is None:
        log.warning("revalidate: site %s no longer exists, dropping job", site_id)
        return
    if site.template.framework != "nextjs":
        log.info("revalidate: %s is a %s site, nothing to do",
                 site.subdomain, site.template.framework)
        return
    if not settings.revalidate_secret:
        log.warning("revalidate: REVALIDATE_SECRET is empty in .env, skipping")
        return

    host = site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}"
    paths = payload.get("paths") or ["/"]

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.post(
                f"https://{host}/api/revalidate",
                json={"paths": paths},
                # Header, not a query string: a secret in a URL ends up in access
                # logs, browser history and referrer headers.
                headers={"x-revalidate-secret": settings.revalidate_secret},
            )
        log.info("revalidate: %s %s -> %s", host, paths, response.status_code)
    except httpx.HTTPError as exc:
        # Expected while the site is not deployed yet. Not worth a retry storm:
        # the content is already saved, and the next edit will try again.
        log.warning("revalidate: could not reach %s (%s)", host, exc)


async def handle_attach_domain(payload: dict) -> None:
    """Attach a just-published site's subdomain to its template's Vercel
    project — see app/vercel.py's module docstring for the full why.

    Only Next.js sites need this (same reasoning as handle_revalidate_site):
    a Vite template fetches its config client-side from whatever host it's
    already deployed at, so there's no "which deployment serves this
    hostname" question to solve for it.
    """
    from app import vercel

    site_id = payload.get("site_id")
    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()

    if site is None:
        log.warning("attach_domain: site %s no longer exists, dropping job", site_id)
        return
    if site.template.framework != "nextjs":
        return
    project_id = site.template.vercel_project_id
    if not project_id:
        log.info(
            "attach_domain: template %s has no vercel_project_id set, skipping",
            site.template.key,
        )
        return

    host = site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}"
    await vercel.add_domain_to_project(host, project_id)


async def handle_detach_domain(payload: dict) -> None:
    """Remove a domain a site no longer uses from its template's Vercel
    project — see app/vercel.py's remove_domain_from_project.

    The domain to remove is carried in the payload (payload["domain"]), not
    re-derived from the site record: by the time this job runs, the site's
    custom_domain has already been overwritten in the database with the NEW
    value (or null) — the old one only still exists in this message.
    """
    from app import vercel

    site_id = payload.get("site_id")
    domain = payload.get("domain")
    if not domain:
        return

    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()

    if site is None:
        log.warning("detach_domain: site %s no longer exists, dropping job", site_id)
        return
    project_id = site.template.vercel_project_id
    if not project_id:
        return

    await vercel.remove_domain_from_project(domain, project_id)


async def handle_generate_sitemap(payload: dict) -> None:
    """Placeholder for search-engine pings after a publish.

    The sitemap itself is generated on demand by /public/site/{host}/sitemap.xml,
    so there is nothing to build here. When you want it, this is where you would
    ping Google/Bing that the sitemap changed.
    """
    log.info("sitemap: site %s (served live, nothing to build)", payload.get("site_id"))


async def handle_send_email(payload: dict) -> None:
    """Real send via app/mailer.py (Hostinger SMTP) — this is exactly the
    "slow email send never makes a user wait" case this module's own
    docstring describes. Callers (app/api/leads.py) queue this instead of
    awaiting mailer.send_email() inline, which is what made signup take
    7+ seconds before this existed."""
    from app import mailer

    sent = await mailer.send_email(
        payload["to"], payload["subject"], payload["html_body"], payload["text_body"]
    )
    if not sent:
        log.warning("email: failed to send to %s (subject=%s)", payload.get("to"), payload.get("subject"))


async def handle_send_order_notifications(payload: dict) -> None:
    """Dashboard bell notification + web push for a new/blocked order.

    Deliberately NOT run inline in create_public_order (app/api/public.py) —
    a webpush() call per subscribed browser is a real network round trip
    (100-500ms+ to Chrome/Firefox/etc's push service), and the notification
    insert is its own DB commit. Doing either synchronously before answering
    the customer's checkout request added that latency to every order, and
    it gets worse under load the more tenants are checking out at once —
    this worker drains independently of API request threads.
    """
    from app import notifications, push  # local: avoids a worker/API import cycle

    async with SessionLocal() as db:
        await notifications.notify(
            db,
            tenant_id=uuid.UUID(payload["tenant_id"]),
            site_id=uuid.UUID(payload["site_id"]),
            type=payload["type"],
            title=payload["title"],
            body=payload.get("body", ""),
            link=payload.get("link"),
        )
        if payload.get("send_push"):
            await push.send_order_push(
                db,
                site_id=uuid.UUID(payload["site_id"]),
                title=payload["title"],
                body=payload.get("body", ""),
                url=payload.get("link") or "/orders",
            )


async def handle_send_order_email(payload: dict) -> None:
    """Order-confirmation email to the tenant owner — see
    queue.JOB_SEND_ORDER_EMAIL's own comment for why this is a separate job
    from handle_send_order_notifications above (same "don't slow the
    checkout response" reasoning, just for the owner's email instead of the
    dashboard bell/push)."""
    from app import crud, mailer

    order_id = payload["order_id"]
    async with SessionLocal() as db:
        order = (
            await db.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if order is None:
            return

        site = (
            await db.execute(select(Site).where(Site.id == order.site_id))
        ).scalar_one_or_none()
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == order.tenant_id))
        ).scalar_one_or_none()
        if site is None or tenant is None:
            return

        # Tenant.notifications defaults every key to on except marketing —
        # the toggle UI may never have been touched, and "never sent a real
        # order email until someone opens a settings page" is the wrong
        # default.
        if not tenant.notifications.get("orders", True):
            return

        owner = (
            await db.execute(
                select(User).where(User.tenant_id == tenant.id, User.role == "owner")
            )
        ).scalars().first()
        if owner is None:
            return

        product_ids = [i.product_id for i in order.items if i.product_id]
        images_by_id: dict[uuid.UUID, str | None] = {}
        category_by_id: dict[uuid.UUID, str | None] = {}
        if product_ids:
            products = (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars().all()
            category_ids = [p.category_id for p in products if p.category_id]
            categories_by_cat_id: dict[uuid.UUID, str] = {}
            if category_ids:
                cats = (
                    await db.execute(select(Category).where(Category.id.in_(category_ids)))
                ).scalars().all()
                categories_by_cat_id = {c.id: c.name for c in cats}
            for p in products:
                images_by_id[p.id] = p.images[0].get("url") if p.images else None
                category_by_id[p.id] = (
                    categories_by_cat_id.get(p.category_id) if p.category_id else None
                )

        items = [
            {
                "name": item.name_snapshot,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "total_cents": item.total_cents,
                "image_url": images_by_id.get(item.product_id) if item.product_id else None,
                "category": category_by_id.get(item.product_id) if item.product_id else None,
                # Read straight off OrderItem's own snapshot columns — no
                # extra query, and it keeps showing correctly even after
                # the event is later edited or deleted (CLAUDE.md rule 8).
                "event_name": item.event_name_snapshot,
                "event_discount_percent": item.event_discount_percent_snapshot,
            }
            for item in order.items
        ]

        shop_name = (site.business or {}).get("name") or site.name
        shop_domain = site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}"

        # order.customer is a free-form checkout payload — key names vary by
        # storefront theme, same reasoning as crud.extract_customer_phone.
        raw_customer = order.customer or {}
        customer_name = " ".join(
            str(raw_customer.get(k, "")).strip() for k in ("first_name", "last_name")
        ).strip()
        customer_address = (
            raw_customer.get("address")
            or raw_customer.get("shipping_address")
            or raw_customer.get("street_address")
        )
        customer = {
            "name": customer_name or None,
            "phone": crud.extract_customer_phone(raw_customer) or None,
            "email": raw_customer.get("email"),
            "address": customer_address,
        }

        subject, html_body, text_body = mailer.order_created_email(
            shop_name=shop_name,
            order_number=order.order_number,
            items=items,
            total_cents=order.total_cents,
            currency=order.currency,
            order_link=f"{mailer.DASHBOARD}/orders?highlight={order.id}",
            recipient_name=owner.full_name,
            customer=customer,
            shop_domain=shop_domain,
        )
        sent = await mailer.send_email(owner.email, subject, html_body, text_body)
        if not sent:
            log.warning("order email: failed to send to %s (order=%s)", owner.email, order.order_number)


async def handle_send_low_stock_email(payload: dict) -> None:
    """One product just crossed down to the low-stock line — see
    queue.JOB_SEND_LOW_STOCK_EMAIL for why this is queued (small payload,
    handler does its own lookups) rather than rendered inline in
    create_public_order."""
    from app import mailer

    product_id = payload["product_id"]
    async with SessionLocal() as db:
        product = (
            await db.execute(select(Product).where(Product.id == product_id))
        ).scalar_one_or_none()
        if product is None:
            return

        site = (
            await db.execute(select(Site).where(Site.id == product.site_id))
        ).scalar_one_or_none()
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == product.tenant_id))
        ).scalar_one_or_none()
        if site is None or tenant is None:
            return

        if not tenant.notifications.get("low_stock", True):
            return

        owner = (
            await db.execute(
                select(User).where(User.tenant_id == tenant.id, User.role == "owner")
            )
        ).scalars().first()
        if owner is None:
            return

        category_name = None
        if product.category_id:
            category = (
                await db.execute(select(Category).where(Category.id == product.category_id))
            ).scalar_one_or_none()
            category_name = category.name if category else None

        subject, html_body, text_body = mailer.low_stock_email(
            product_name=product.name,
            current_stock=product.stock,
            product_link=f"{mailer.DASHBOARD}/products/{product.id}/edit",
            image_url=product.images[0].get("url") if product.images else None,
            category=category_name,
        )
        sent = await mailer.send_email(owner.email, subject, html_body, text_body)
        if not sent:
            log.warning("low stock email: failed to send to %s (product=%s)", owner.email, product.name)


async def handle_generate_invoice_pdf(payload: dict) -> None:
    """Renders app/invoices.py's HTML via headless Chromium and uploads the
    PDF to Cloudinary — same "real browser render, worker-only" reasoning
    as handle_capture_screenshot above. Best-effort: a rendering/upload
    failure leaves Invoice.pdf_url null rather than crashing the job loop;
    the invoice row itself (already created by the caller) is unaffected."""
    from playwright.async_api import async_playwright

    from app import invoices as invoices_module
    from app import mailer, media

    invoice_id = payload["invoice_id"]
    async with SessionLocal() as db:
        invoice = (
            await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        ).scalar_one_or_none()
        if invoice is None:
            return
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == invoice.tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            return
        owner = (
            await db.execute(
                select(User).where(User.tenant_id == tenant.id, User.role == "owner")
            )
        ).scalars().first()
        site = (
            await db.execute(select(Site).where(Site.tenant_id == tenant.id))
        ).scalars().first()
        site_domain = (
            (site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}")
            if site is not None
            else None
        )

        html = invoices_module.invoice_html(
            invoice_number=invoice.invoice_number,
            plan=invoice.plan,
            amount_cents=invoice.amount_cents,
            currency=invoice.currency,
            period_label=invoice.period_label,
            issued_at=invoice.issued_at.strftime("%d %b %Y"),
            tenant_name=tenant.name,
            tenant_business=invoice.tenant_business_snapshot,
            owner_name=owner.full_name if owner is not None else None,
            owner_email=owner.email if owner is not None else None,
            owner_phone=owner.phone if owner is not None else None,
            site_domain=site_domain,
        )

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                pdf_bytes = await page.pdf(format="A4", print_background=True)
            finally:
                await browser.close()
        uploaded = media.upload_invoice_pdf(pdf_bytes, invoice_number=invoice.invoice_number)
    except Exception as exc:  # noqa: BLE001 — Playwright/Cloudinary both raise a wide variety of exception types
        log.warning("invoice pdf: could not generate %s (%s)", invoice.invoice_number, exc)
        return

    async with SessionLocal() as db:
        invoice = (
            await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        ).scalar_one_or_none()
        if invoice is None:
            return
        invoice.pdf_url = uploaded["url"]
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == invoice.tenant_id))
        ).scalar_one_or_none()
        await db.commit()
    log.info("invoice pdf: generated %s -> %s", invoice.invoice_number, uploaded["url"])

    if tenant is not None and tenant.notifications.get("billing", True):
        if owner is not None:
            subject, html_body, text_body = mailer.invoice_email(
                tenant_name=tenant.name,
                invoice_number=invoice.invoice_number,
                plan_name=invoices_module.PLAN_NAMES.get(invoice.plan, invoice.plan.title()),
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                period_label=invoice.period_label,
                issued_at=invoice.issued_at.strftime("%d %b %Y"),
            )
            sent = await mailer.send_email(
                owner.email,
                subject,
                html_body,
                text_body,
                attachment=(f"{invoice.invoice_number}.pdf", pdf_bytes),
            )
            if not sent:
                log.warning("invoice email: failed to send to %s (invoice=%s)", owner.email, invoice.invoice_number)


async def handle_send_meta_capi_event(payload: dict) -> None:
    """Server-side Meta Purchase event for one just-placed order.

    Best-effort, same instinct as every other queue handler that talks to a
    third party (handle_capture_screenshot below, handle_revalidate_site
    above): a Meta outage or a bad/expired access token must never affect an
    order that already succeeded — log and drop.
    """
    from app import courier_crypto, marketing

    site_id = payload.get("site_id")
    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()
        if site is None:
            log.warning("meta capi: site %s no longer exists, dropping job", site_id)
            return

        pixel_id = (site.seo or {}).get("facebook_pixel")
        if not pixel_id:
            # Pixel was disconnected after the order was placed but before
            # this job drained — nothing to send to.
            return

        connection = (
            await db.execute(
                select(MarketingConnection).where(
                    MarketingConnection.site_id == site.id,
                    MarketingConnection.provider == "meta_capi",
                    MarketingConnection.status == "connected",
                )
            )
        ).scalar_one_or_none()
        if connection is None:
            return

        try:
            access_token = courier_crypto.decrypt(connection.access_token_encrypted)
        except Exception:
            log.warning("meta capi: could not decrypt token for site %s, dropping job", site_id)
            return

    ok, error = await marketing.send_purchase_event(
        pixel_id=pixel_id,
        access_token=access_token,
        event_id=payload["event_id"],
        event_time=payload["event_time"],
        value=payload["value"],
        currency=payload["currency"],
        order_number=payload["order_number"],
        customer_phone=payload.get("customer_phone"),
        customer_email=payload.get("customer_email"),
        client_ip=payload.get("client_ip"),
        user_agent=payload.get("user_agent"),
        event_source_url=payload.get("event_source_url"),
    )
    if not ok:
        log.warning("meta capi: send failed for site %s order %s: %s", site_id, payload.get("order_number"), error)


async def handle_capture_screenshot(payload: dict) -> None:
    """Mobile-viewport screenshot of the just-published storefront, shown on
    the Themes page card (see app/screenshot.py, theme-card.tsx).

    Best-effort: a screenshot failure (site not resolvable yet, Chromium
    timeout, whatever) must not affect the publish that already succeeded —
    log and drop, same as every other queue handler that talks to something
    outside our own infra (handle_revalidate_site above is the same shape).
    """
    from app import media, screenshot

    site_id = payload.get("site_id")
    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()

    if site is None:
        log.warning("screenshot: site %s no longer exists, dropping job", site_id)
        return

    # Prefer the subdomain over a custom_domain: the worker's own revalidate
    # call (handle_revalidate_site, queued alongside this job) targets the
    # subdomain, and a merchant's custom domain typically has its own DNS/CDN
    # layer with slower, less predictable propagation on top of that. Using
    # whichever host actually gets revalidated means the screenshot reliably
    # reflects the just-published content instead of racing a slower path.
    host = f"{site.subdomain}.{settings.site_base_domain}"

    # This job is queued in the same breath as JOB_REVALIDATE_SITE, right
    # when publish_site commits — capturing immediately would very likely
    # screenshot the PRE-publish page, since CDN/revalidation propagation
    # isn't instant. 90s is real room above the typical build+propagate
    # time without holding the job queue for as long as the previous 8
    # minutes did.
    #
    # Cost: this handler holds one of the worker's 5 prefetched slots (see
    # main()'s set_qos) for the whole wait. At soft-launch volume that's
    # fine; if 5+ tenants ever publish within the same ~90s window, a 6th
    # unrelated job (order email, revalidate, whatever) would have to wait
    # for a slot to free up. Worth switching to a proper delayed-requeue
    # instead of a blocking sleep if that ever becomes a real bottleneck.
    await asyncio.sleep(90)

    try:
        png = await screenshot.capture_mobile_screenshot(f"https://{host}")
        # "_system" is deliberately not one of media.VALID_CATEGORIES — this
        # is infra-generated, not a merchant upload, so it must never count
        # against the tenant's plan storage quota or show up in their Media
        # Library listing (both only ever iterate VALID_CATEGORIES).
        # upload_site_screenshot (not upload_image directly) also prunes
        # this folder down to media.SITE_SCREENSHOT_KEEP files — every
        # publish captures a new screenshot and nothing else ever deleted
        # the old ones, so this folder grew unbounded before that existed.
        uploaded = media.upload_site_screenshot(png, subdomain=site.subdomain)
    except Exception as exc:  # noqa: BLE001 — Playwright/Cloudinary both raise
        # a wide variety of exception types here; any of them is equally
        # "couldn't get a screenshot this time," never worth crashing the job
        # loop over.
        log.warning("screenshot: could not capture %s (%s)", host, exc)
        return

    async with SessionLocal() as db:
        site = (
            await db.execute(select(Site).where(Site.id == site_id))
        ).scalar_one_or_none()
        if site is None:
            return
        site.screenshot_url = uploaded["url"]
        await db.commit()
    log.info("screenshot: captured %s -> %s", host, uploaded["url"])


HANDLERS = {
    JOB_REVALIDATE_SITE: handle_revalidate_site,
    JOB_GENERATE_SITEMAP: handle_generate_sitemap,
    JOB_SEND_EMAIL: handle_send_email,
    JOB_SEND_ORDER_NOTIFICATIONS: handle_send_order_notifications,
    JOB_SEND_ORDER_EMAIL: handle_send_order_email,
    JOB_SEND_LOW_STOCK_EMAIL: handle_send_low_stock_email,
    JOB_GENERATE_INVOICE_PDF: handle_generate_invoice_pdf,
    JOB_ATTACH_DOMAIN: handle_attach_domain,
    JOB_DETACH_DOMAIN: handle_detach_domain,
    JOB_CAPTURE_SCREENSHOT: handle_capture_screenshot,
    JOB_SEND_META_CAPI_EVENT: handle_send_meta_capi_event,
}


# ---------------------------------------------------------------------------
#  Notification retention — a plain periodic sweep, not a queue job.
#
#  Notifications are ephemeral operational data, not business records like
#  orders — there's no reason to keep them forever, and an unbounded table
#  only gets slower to query/index over time. 15-day TTL, checked once a day
#  (that cadence is plenty for a 15-day window — no need to check hourly).
#  See migrations/023_notifications_ttl_index.sql for the index this sweep
#  relies on.
# ---------------------------------------------------------------------------

NOTIFICATION_TTL_DAYS = 15
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


async def cleanup_old_notifications() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=NOTIFICATION_TTL_DAYS)
    async with SessionLocal() as db:
        result = await db.execute(delete(Notification).where(Notification.created_at < cutoff))
        await db.commit()
        if result.rowcount:
            log.info(
                "cleanup: deleted %d notification(s) older than %d days",
                result.rowcount, NOTIFICATION_TTL_DAYS,
            )


async def notification_cleanup_loop() -> None:
    # Runs once immediately on startup (so a long-idle worker catches up),
    # then once a day thereafter.
    while True:
        try:
            await cleanup_old_notifications()
        except Exception:  # noqa: BLE001 - a failed sweep must not kill the worker
            log.exception("notification cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
#  Trial expiry sweep — the only place expired trial tenants actually get
#  deleted. Login itself already rejects a trial past trial_expires_at (see
#  app/api/auth.py's _check_tenant_access), so this isn't what blocks
#  access; it's just the cleanup, running trial_grace_days later. Every FK
#  to tenants.id in this schema is ondelete="CASCADE", so one DELETE here
#  takes the tenant's site/products/orders/etc. with it — no manual
#  multi-table cleanup needed.
# ---------------------------------------------------------------------------

TRIAL_SWEEP_INTERVAL_SECONDS = 60 * 60  # hourly


async def sweep_expired_trials() -> None:
    from app import cache

    cutoff = datetime.now(UTC) - timedelta(days=settings.trial_grace_days)
    async with SessionLocal() as db:
        expiring_tenant_ids = (
            await db.execute(
                select(Tenant.id).where(
                    Tenant.plan == "trial", Tenant.trial_expires_at < cutoff
                )
            )
        ).scalars().all()
        if not expiring_tenant_ids:
            return

        # Read every affected site's hostnames BEFORE the cascade delete —
        # once the rows are gone there's nothing left to read them from.
        # Without this, the storefront cache (keyed by subdomain) keeps
        # serving a deleted trial's theme/template to anyone who later
        # reuses the same subdomain, until the cache TTL expires — a real
        # bug (see app/api/superadmin.py::delete_tenant's own fix for the
        # single-tenant version of this same gap).
        sites = (
            await db.execute(select(Site).where(Site.tenant_id.in_(expiring_tenant_ids)))
        ).scalars().all()

        result = await db.execute(delete(Tenant).where(Tenant.id.in_(expiring_tenant_ids)))
        await db.commit()
        if result.rowcount:
            log.info("trial sweep: deleted %d expired trial tenant(s)", result.rowcount)

    for site in sites:
        await cache.invalidate_site(site.subdomain, site.custom_domain)


async def trial_sweep_loop() -> None:
    while True:
        try:
            await sweep_expired_trials()
        except Exception:  # noqa: BLE001 - a failed sweep must not kill the worker
            log.exception("trial expiry sweep failed")
        await asyncio.sleep(TRIAL_SWEEP_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
#  Consumer loop
# ---------------------------------------------------------------------------


async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    # `requeue=False` in the except block below is deliberate: a message that
    # fails because of a BUG will fail again, forever, blocking the queue. Better
    # to log it and move on. Real systems send these to a dead-letter queue —
    # a good next exercise once you are comfortable here.
    async with message.process(requeue=False):
        try:
            body = json.loads(message.body)
            job_type, payload = body.get("type"), body.get("payload", {})
        except json.JSONDecodeError:
            log.error("dropping unparseable message: %r", message.body[:200])
            return

        handler = HANDLERS.get(job_type)
        if handler is None:
            log.error("no handler for job type %r - dropping", job_type)
            return

        log.info("-> %s", job_type)
        try:
            await handler(payload)
        except Exception:  # noqa: BLE001
            log.exception("job %s failed", job_type)


async def main() -> None:
    log.info("connecting to %s", settings.rabbitmq_url)
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)

    async with connection:
        channel = await connection.channel()
        # prefetch_count=5: take at most 5 unacked messages at a time. Without a
        # limit, one worker grabs the entire queue and a second worker sits idle.
        await channel.set_qos(prefetch_count=5)
        q = await channel.declare_queue(settings.queue_name, durable=True)

        log.info("listening on '%s'. Ctrl+C to stop.", settings.queue_name)
        await q.consume(on_message)
        asyncio.create_task(notification_cleanup_loop())
        asyncio.create_task(trial_sweep_loop())
        await asyncio.Future()  # sleep forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        asyncio.run(engine.dispose())
