"""Keeps each store's IP blocklist in Vercel Edge Config.

Why: storefront middleware has to check every page view against the store's
blocklist. It used to ask this API each time, which was a large share of both
Vercel usage and API load, for a list that almost never changes and is empty
for most stores. Edge Config is a read-optimised store on Vercel's network, so
the check now happens there and this module pushes the list whenever it changes.

The database stays the source of truth, and app/main.py's ip_block middleware
still enforces blocks on every /public/* call. A failed push here therefore
leaves the page-level check stale, never the store broken, so it logs and
swallows like cache.py and queue.py (CLAUDE.md rule 9).

Run scripts/sync_ip_blocks.py to rebuild every store's list from the database.
"""

import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import FraudIpBlocklistEntry, Site

log = logging.getLogger(__name__)


def key_for(host: str) -> str:
    """Edge Config keys allow only letters, digits, _ and -. Must match
    blockedByEdgeConfig in templates/*/middleware.ts."""
    return "ipb_" + re.sub(r"[^a-z0-9]", "_", host.lower())


def hosts_for(site: Site) -> list[str]:
    hosts = [f"{site.subdomain}.{settings.site_base_domain}"]
    if site.custom_domain:
        hosts.append(site.custom_domain)
    return hosts


async def sync_ip_blocks(db: AsyncSession, site: Site) -> None:
    """Writes this store's current blocklist under each of its hostnames."""
    if not (settings.edge_config_id and settings.vercel_api_token):
        return
    try:
        ips = (
            await db.execute(
                select(FraudIpBlocklistEntry.ip_address).where(
                    FraudIpBlocklistEntry.tenant_id == site.tenant_id,
                    FraudIpBlocklistEntry.site_id == site.id,
                )
            )
        ).scalars().all()
        # upsert, not delete, for an empty list: deleting a key that was never
        # written is an API error, and an empty list reads the same.
        items = [{"operation": "upsert", "key": key_for(h), "value": list(ips)} for h in hosts_for(site)]
        params = {"teamId": settings.vercel_team_id} if settings.vercel_team_id else {}
        async with httpx.AsyncClient(timeout=10.0) as http:
            res = await http.patch(
                f"https://api.vercel.com/v1/edge-config/{settings.edge_config_id}/items",
                params=params,
                headers={"Authorization": f"Bearer {settings.vercel_api_token}"},
                json={"items": items},
            )
        if res.status_code >= 300:
            log.warning("edge config sync failed for site %s: %s", site.id, res.status_code)
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("edge config sync failed for site %s: %s", site.id, exc)
