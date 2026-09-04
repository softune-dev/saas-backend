"""One-off backlog cleanup: detach Vercel domains left over from tenants
deleted BEFORE app/api/superadmin.py::delete_tenant and
app/worker.py::sweep_expired_trials learned to call
vercel.remove_domain_from_project themselves. Going forward neither path
leaves an orphan; this script only clears the pre-existing backlog.

Same superadmin dashboard page (Superadmin -> Vercel Cleanup) does this
same job through a real UI now — this script is the CLI/one-off
equivalent, sharing the exact same matching logic via
app.vercel.orphaned_domains_report (do not re-derive that logic here;
see its own docstring for why that matters).

Defaults to DRY RUN — prints exactly what it would detach and does
nothing else. Pass --confirm to actually call Vercel's detach API.

Usage:
    venv\\Scripts\\python.exe scripts\\cleanup_orphaned_vercel_domains.py            # dry run
    venv\\Scripts\\python.exe scripts\\cleanup_orphaned_vercel_domains.py --confirm  # actually detach
"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app import vercel
from app.db import SessionLocal
from app.models import Template


async def main() -> None:
    confirm = "--confirm" in sys.argv

    async with SessionLocal() as db:
        templates = (
            await db.execute(
                select(Template).where(Template.is_active, Template.framework == "nextjs")
            )
        ).scalars().all()

        for template in templates:
            if not template.vercel_project_id:
                continue

            print(f"\n=== {template.key} (project {template.vercel_project_id}) ===")
            report = await vercel.orphaned_domains_report(db, template)

            if report["review"]:
                print(f"  {len(report['review'])} domain(s) attached — not touched automatically, review manually:")
                for d in report["review"]:
                    print(f"    - {d}")

            if not report["orphaned"]:
                print("  no orphaned subdomains found")
                continue

            print(f"  {len(report['orphaned'])} orphaned subdomain(s):")
            for host in report["orphaned"]:
                if confirm:
                    ok = await vercel.remove_domain_from_project(host, template.vercel_project_id)
                    print(f"    {'detached' if ok else 'FAILED to detach'}: {host}")
                else:
                    print(f"    would detach: {host}")

    if not confirm:
        print("\nDry run only — nothing was changed. Re-run with --confirm to actually detach.")


if __name__ == "__main__":
    asyncio.run(main())
