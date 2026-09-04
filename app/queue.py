"""RabbitMQ publisher — hands slow work to the background worker.

WHY A QUEUE AT ALL: when a user clicks "Publish", the things that must happen are
(1) write to the database and (2) tell Vercel to refresh that site's cache, maybe
(3) regenerate a sitemap, (4) send an email. Only #1 needs to finish before the
HTTP response. Doing the rest inline means the user stares at a spinner while we
wait on somebody else's network — and if Vercel is slow, OUR api looks broken.

So: publish a job, return 200 immediately, let app/worker.py do the waiting.

DURABILITY: the queue and messages are marked persistent, so a RabbitMQ restart
does not silently drop queued work.

DEGRADES GRACEFULLY: publish() swallows connection errors. Losing a cache-refresh
job should not fail the user's save — the content IS saved, it just appears a bit
later. Anything that must never be lost belongs in the database, not only here.
"""

import json
import logging

import aio_pika

from app.config import settings

log = logging.getLogger(__name__)

_conn: aio_pika.abc.AbstractRobustConnection | None = None
_channel: aio_pika.abc.AbstractChannel | None = None

# Job type constants — import these instead of typing the strings, so a typo is
# an ImportError at startup rather than a message no consumer ever picks up.
JOB_REVALIDATE_SITE = "revalidate_site"
JOB_SEND_EMAIL = "send_email"
JOB_GENERATE_SITEMAP = "generate_sitemap"
# Dashboard bell notification + web push for a new/blocked order. Queued
# instead of run inline in create_public_order — see worker.py's handler for
# why: a webpush() call is a real network round trip to Chrome/Firefox's
# push service, and doing that before responding to the customer's checkout
# request made every order slower, worse the more tenants place orders
# concurrently.
JOB_SEND_ORDER_NOTIFICATIONS = "send_order_notifications"
# Order-confirmation email to the tenant owner. Separate from
# JOB_SEND_ORDER_NOTIFICATIONS (bell/push) for the same reason that job is
# queued at all: rendering the email needs a few extra DB lookups (owner
# User row, product images for the line items) that have no business
# happening before the customer's checkout response goes out. Payload is
# just the order id — the handler re-fetches everything else, same as
# handle_send_order_notifications does.
JOB_SEND_ORDER_EMAIL = "send_order_email"
# Sent once when a product's stock crosses DOWN to _LOW_STOCK_THRESHOLD
# (app/api/public.py) — not on every order after, only that one transition.
# Payload is just the product id, same "handler re-fetches everything"
# shape as JOB_SEND_ORDER_EMAIL.
JOB_SEND_LOW_STOCK_EMAIL = "send_low_stock_email"
# Renders app/invoices.py's HTML via Playwright and uploads the resulting
# PDF to Cloudinary (see app/media.py's upload_invoice_pdf) — same "real
# browser render, worker-only" reasoning as JOB_CAPTURE_SCREENSHOT. Queued
# right after an Invoice row is created (trial start, or a manual plan
# change) so the row exists immediately even though pdf_url fills in a
# moment later.
JOB_GENERATE_INVOICE_PDF = "generate_invoice_pdf"
# Attach a newly-published site's subdomain to its template's Vercel
# project — see app/vercel.py's module docstring for why this exists.
JOB_ATTACH_DOMAIN = "attach_domain"
# Mirror of the above: detach a domain a site no longer uses (custom_domain
# changed or was cleared) so it stops serving that site's storefront.
JOB_DETACH_DOMAIN = "detach_domain"
# Mobile-viewport screenshot of the live storefront, for the Themes page
# card — see app/screenshot.py and worker.py's handler.
JOB_CAPTURE_SCREENSHOT = "capture_screenshot"
# Server-side Meta Conversions API Purchase event — see app/marketing.py and
# worker.py's handler. Queued instead of awaited inline in create_public_order
# for the same reason as JOB_SEND_ORDER_NOTIFICATIONS: it's a real network
# call to a third party, and it must never slow down or fail the customer's
# actual checkout.
JOB_SEND_META_CAPI_EVENT = "send_meta_capi_event"
# Auto-book a freshly placed storefront order with the site's connected
# Steadfast account — only queued when the site's courier_rules.auto_book is
# enabled (see app/api/public.py's create_public_order and
# app/courier_booking.py). Queued for the same reason as
# JOB_SEND_ORDER_NOTIFICATIONS: booking is a real network call to a third
# party and must never slow down or fail the customer's own checkout
# response.
JOB_BOOK_COURIER = "book_courier"
# One-time WhatsApp welcome message, sent from Softunebd's own business
# number right after signup — see app/whatsapp.py. Queued (never awaited
# inline) for the same reason as every email send in this file: a real
# network call to a third party must never slow down or fail the signup
# response itself. Silently skipped by the handler if the merchant gave no
# phone number, or if WhatsApp isn't configured.
JOB_SEND_WHATSAPP_WELCOME = "send_whatsapp_welcome"


async def connect() -> aio_pika.abc.AbstractChannel:
    """Lazily open a robust connection. `connect_robust` auto-reconnects, so a
    RabbitMQ restart heals itself without restarting the API."""
    global _conn, _channel
    if _channel is None or _channel.is_closed:
        _conn = await aio_pika.connect_robust(settings.rabbitmq_url, timeout=5)
        _channel = await _conn.channel()
        await _channel.declare_queue(settings.queue_name, durable=True)
    return _channel


async def close() -> None:
    global _conn, _channel
    if _conn is not None and not _conn.is_closed:
        await _conn.close()
    _conn, _channel = None, None


async def publish(job_type: str, payload: dict) -> bool:
    """Enqueue a job. Returns True if it made it onto the queue.

    Callers ignore the return value in normal flow — it exists so tests can
    assert a job was published.
    """
    body = json.dumps({"type": job_type, "payload": payload}, default=str)
    try:
        channel = await connect()
        await channel.default_exchange.publish(
            aio_pika.Message(
                body.encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=settings.queue_name,
        )
        log.info("queued %s", job_type)
        return True
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("could not queue %s (%s). Is RabbitMQ running?", job_type, exc)
        return False
