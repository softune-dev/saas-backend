"""Rebuilds Edge Config's IP blocklists from the database. Reads the database,
writes only to Edge Config. Needs EDGE_CONFIG_ID, VERCEL_API_TOKEN and
VERCEL_TEAM_ID. Run once after setup, or any time the two look out of step:

    python -m scripts.sync_ip_blocks
"""

import asyncio

from sqlalchemy import select

from app import edge_config
from app.config import settings
from app.db import SessionLocal
from app.models import FraudIpBlocklistEntry, Site


async def main() -> None:
    if not settings.edge_config_id:
        raise SystemExit("EDGE_CONFIG_ID is not set")
    async with SessionLocal() as db:
        ids = (await db.execute(select(FraudIpBlocklistEntry.site_id).distinct())).scalars().all()
        sites = (await db.execute(select(Site).where(Site.id.in_(ids)))).scalars().all() if ids else []
        for site in sites:
            await edge_config.sync_ip_blocks(db, site)
    print(f"Synced {len(sites)} stores with blocked IPs.")


if __name__ == "__main__":
    asyncio.run(main())
