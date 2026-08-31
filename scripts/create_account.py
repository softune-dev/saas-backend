"""Create a paid customer's account after payment is received.

Public self-signup is closed (see app/api/auth.py) — this is a paid-only
service on a public domain, and an open /register endpoint let anyone create
a free account with no payment step. Until real billing exists, this script
is the only way a new account gets created: run it by hand once you've
actually received payment for the customer.

This creates the FULL account in one go — workspace, owner login, plan, AND
the one site tied to it. This system has no separate "which templates can
this tenant use" permission: a tenant gets exactly one site, tied to exactly
one template, decided right here at creation (the dashboard has no UI that
ever creates a site — see themes-grid.tsx's own comment). So the plan and
template aren't optional extras; they're the actual product being sold.

Usage — just run it, it asks for everything it needs:
    venv\\Scripts\\python.exe scripts\\create_account.py

Prints the new tenant/user/site ids on success so you can hand the login
details to the customer and, if needed, look them up again later.
"""

import asyncio
import getpass
import sys

sys.path.insert(0, ".")

from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.crud import create_tenant_owner_and_site
from app.db import SessionLocal
from app.models import Template
from app.schemas import SiteCreate
from fastapi import HTTPException

PLAN_CHOICES = ["starter", "growth", "business", "demo"]


def prompt_password() -> str:
    while True:
        password = getpass.getpass("Password (8-72 chars, hidden while typing): ")
        if 8 <= len(password) <= 72:
            confirm = getpass.getpass("Confirm password: ")
            if password == confirm:
                return password
            print("Passwords didn't match, try again.\n")
        else:
            print("Password must be 8-72 characters.\n")


def prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print(f"{label} can't be empty.")


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    options = "/".join(c if c != default else c.upper() for c in choices)
    while True:
        value = input(f"{label} [{options}]: ").strip().lower() or default
        if value in choices:
            return value
        print(f"Must be one of: {', '.join(choices)}")


def prompt_subdomain(name_hint: str) -> str:
    """Validates against the exact same rule POST /sites enforces
    (SiteCreate's field pattern + reserved-word check) so a script-created
    site can never violate a constraint the dashboard would have caught."""
    suggested = name_hint.lower().replace(" ", "-")
    while True:
        raw = input(f"Subdomain (e.g. {suggested}): ").strip().lower()
        if not raw:
            print("Subdomain can't be empty.")
            continue
        try:
            SiteCreate(template_id="00000000-0000-0000-0000-000000000000", name="x", subdomain=raw)
        except ValidationError as exc:
            print(exc.errors()[0]["msg"])
            continue
        return raw


async def prompt_template_key(db) -> str:
    templates = (await db.execute(select(Template).where(Template.is_active))).scalars().all()
    keys = [t.key for t in templates]
    if not keys:
        print("No active templates found — nothing to provision onto.")
        raise SystemExit(1)
    print(f"Available templates: {', '.join(keys)}")
    while True:
        key = input("Template key: ").strip().lower()
        if key in keys:
            return key
        print(f"Must be one of: {', '.join(keys)}")


async def main() -> None:
    print("Create a new paid account (run this after payment is received)\n")
    email = prompt_required("Email")
    password = prompt_password()
    workspace_name = prompt_required("Workspace / company name")
    full_name = input("Customer's full name (optional): ").strip() or None
    plan = prompt_choice("Plan", PLAN_CHOICES, default="starter")

    async with SessionLocal() as db:
        template_key = await prompt_template_key(db)
        site_name = prompt_required("Site name (shown in the dashboard)")
        subdomain = prompt_subdomain(site_name)

        try:
            user, site = await create_tenant_owner_and_site(
                db,
                email=email,
                password=password,
                workspace_name=workspace_name,
                plan=plan,
                template_key=template_key,
                site_name=site_name,
                subdomain=subdomain,
                full_name=full_name,
            )
        except HTTPException as exc:
            print(f"\nFailed: {exc.detail}")
            raise SystemExit(1)

        print("\nAccount created.")
        print(f"  tenant_id: {user.tenant_id}")
        print(f"  user_id:   {user.id}")
        print(f"  email:     {user.email}")
        print(f"  plan:      {plan}")
        print(f"  site_id:   {site.id}")
        print(f"  template:  {template_key}")
        print(f"  subdomain: {subdomain}.{settings.site_base_domain} (live only after they publish)")


if __name__ == "__main__":
    asyncio.run(main())
