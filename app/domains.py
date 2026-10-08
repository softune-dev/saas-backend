"""Custom domain rules: what counts as a valid domain, and when a requested
domain is allowed to become a site's real one.

THE RULE: sites.custom_domain is only ever a domain that is connected and
serving. A merchant's request goes to sites.pending_custom_domain first (see
migrations/075) and is promoted here once Vercel confirms it is attached to
the site's project and its DNS points at us. Until then the site keeps using
its subdomain (or its previous, still working custom domain), so a typo or a
domain the merchant never set up can't break their storefront.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, queue, vercel
from app.config import settings
from app.models import Site

log = logging.getLogger(__name__)

PENDING_DOMAIN_TTL = timedelta(days=14)

# One or more DNS labels (letters, digits, inner hyphens, max 63 chars each)
# then an alphabetic TLD. This rejects spaces, underscores, IP addresses and
# bare words like "mehedi store" or "localhost" in one pattern.
_HOSTNAME = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")


def normalize_domain(raw: str | None) -> str | None:
    """Clean what a merchant typed into a bare lowercase hostname, or raise
    ValueError with a message safe to show them. Empty means "no domain"."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    value = _SCHEME.sub("", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.rsplit("@", 1)[-1].split(":", 1)[0].rstrip(".")
    if not _HOSTNAME.match(value):
        raise ValueError("Enter a valid domain, like shop.example.com (no spaces).")
    for base in {settings.site_base_domain.lower(), "vercel.app"}:
        if value == base or value.endswith(f".{base}"):
            raise ValueError(
                "That is a Softune address. Enter a domain you own, like shop.example.com."
            )
    return value


async def _drop_caches(*hosts: str | None) -> None:
    for host in hosts:
        if host:
            await cache.drop(cache.site_key(host))


async def promote(db: AsyncSession, site: Site) -> bool:
    """Make the pending domain the site's real one. Call only after the
    domain was confirmed connected. Returns False (and clears the pending
    request) if another site connected the same domain first."""
    pending = site.pending_custom_domain
    if not pending:
        return False
    previous = site.custom_domain
    site.custom_domain = pending
    site.pending_custom_domain = None
    site.pending_domain_requested_at = None
    try:
        await crud.save(db, site)
    except HTTPException:
        await db.rollback()
        site = (await db.execute(select(Site).where(Site.id == site.id))).scalar_one()
        site.pending_custom_domain = None
        site.pending_domain_requested_at = None
        await db.commit()
        log.warning("domain %s was claimed by another site, dropping request", pending)
        return False

    await _drop_caches(pending, previous, f"{site.subdomain}.{settings.site_base_domain}")
    if site.status == "published":
        await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id)})
    if previous and previous != pending:
        # The old domain is no longer this site's; free it on Vercel too.
        await queue.publish(queue.JOB_DETACH_DOMAIN, {"site_id": str(site.id), "domain": previous})
    return True


async def sweep_pending(db: AsyncSession) -> tuple[int, int]:
    """Promote every pending domain that has connected and drop any that sat
    unconnected past PENDING_DOMAIN_TTL. Returns (promoted, expired). Run
    periodically by the worker, so a merchant who finishes their DNS setup
    and closes the tab still gets their domain."""
    sites = (
        await db.execute(select(Site).where(Site.pending_custom_domain.is_not(None)))
    ).scalars().all()
    promoted = expired = 0
    now = datetime.now(UTC)
    for site in sites:
        pending = site.pending_custom_domain
        project_id = site.template.vercel_project_id if site.template else None
        connected = await vercel.check_domain_connected(pending, project_id or "")
        if connected is True:
            if await promote(db, site):
                promoted += 1
            continue
        requested = site.pending_domain_requested_at
        if requested and now - requested > PENDING_DOMAIN_TTL:
            site.pending_custom_domain = None
            site.pending_domain_requested_at = None
            await db.commit()
            await queue.publish(queue.JOB_DETACH_DOMAIN, {"site_id": str(site.id), "domain": pending})
            expired += 1
    return promoted, expired
