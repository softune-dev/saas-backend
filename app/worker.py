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
from app.models import Notification, Site
from app.queue import (
    JOB_ATTACH_DOMAIN,
    JOB_CAPTURE_SCREENSHOT,
    JOB_DETACH_DOMAIN,
    JOB_GENERATE_SITEMAP,
    JOB_REVALIDATE_SITE,
    JOB_SEND_EMAIL,
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
    """Placeholder. Wire up SMTP settings from .env when you need real mail.

    For local development, run maildev (see .env.example section 9) and point
    SMTP_HOST/SMTP_PORT at it — you get a web inbox instead of real sending.
    """
    log.info("email: to=%s subject=%s (not sent - no SMTP configured)",
             payload.get("to"), payload.get("subject"))


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

    # TEMPORARY REVERT: prefer custom_domain again. The free-subdomain-first
    # change (subdomain.{site_base_domain}) broke this in practice —
    # SITE_BASE_DOMAIN in .env is currently "vercel.app", a placeholder that
    # was never updated to the real value, so every subdomain capture hit a
    # real Vercel account's DEPLOYMENT_NOT_FOUND 404 page instead of a real
    # site. Switch back to subdomain-first once SITE_BASE_DOMAIN is confirmed
    # and corrected — see this function's git history for the intended change.
    host = site.custom_domain or f"{site.subdomain}.{settings.site_base_domain}"

    # This job is queued in the same breath as JOB_REVALIDATE_SITE, right
    # when publish_site commits — capturing immediately would very likely
    # screenshot the PRE-publish page, since CDN/revalidation propagation
    # isn't instant. Deliberately generous (not just matching the 1-2 min
    # publish-toast copy) — give slower propagation real room before giving
    # up and capturing stale content anyway.
    #
    # Cost: this handler holds one of the worker's 5 prefetched slots (see
    # main()'s set_qos) for the whole wait. At soft-launch volume that's
    # fine; if 5+ tenants ever publish within the same ~8-minute window,
    # a 6th unrelated job (order email, revalidate, whatever) would have to
    # wait for a slot to free up. Worth switching to a proper delayed-requeue
    # instead of a blocking sleep if that ever becomes a real bottleneck.
    await asyncio.sleep(8 * 60)

    try:
        png = await screenshot.capture_mobile_screenshot(f"https://{host}")
        # "_system" is deliberately not one of media.VALID_CATEGORIES — this
        # is infra-generated, not a merchant upload, so it must never count
        # against the tenant's plan storage quota or show up in their Media
        # Library listing (both only ever iterate VALID_CATEGORIES).
        uploaded = media.upload_image(png, subdomain=site.subdomain, category="_system")
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
    JOB_ATTACH_DOMAIN: handle_attach_domain,
    JOB_DETACH_DOMAIN: handle_detach_domain,
    JOB_CAPTURE_SCREENSHOT: handle_capture_screenshot,
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
        await asyncio.Future()  # sleep forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        asyncio.run(engine.dispose())
