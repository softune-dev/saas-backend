"""Vercel domain automation — attaches a published site's subdomain to the
right Vercel project.

WHY THIS EXISTS: every merchant site gets a clean `{subdomain}.SITE_BASE_DOMAIN`
URL with no template name in it, so an Aurora site and a Bazaar site look
identical at the URL level. Two Vercel projects can't share one wildcard
domain, so instead of DNS-level routing, each site's exact subdomain is
registered directly against its template's Vercel project via this API call —
Vercel's edge then serves that hostname from the correct deployment with no
extra hop. One wildcard DNS record (`*.SITE_BASE_DOMAIN` -> Vercel) still has
to exist for the hostname to resolve at all; this call is what tells Vercel
which project owns it.

DEGRADES GRACEFULLY, same pattern as cache.py/queue.py/push.py: this runs
from the background worker (JOB_ATTACH_DOMAIN), not inline in publish_site,
so a Vercel API hiccup never blocks a merchant from publishing. The site is
already live and servable via its cached config either way — a failed
attach just means the URL needs a manual fix in the Vercel dashboard until
retried.
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_API_BASE = "https://api.vercel.com"


async def add_domain_to_project(domain: str, project_id: str) -> bool:
    """Attach `domain` to the given Vercel project. Returns True on success
    (including "already attached to this project", which is idempotent —
    the worker may retry this job)."""
    if not settings.vercel_api_token or not project_id:
        log.info(
            "vercel: skipping domain attach for %s (token or project id not configured)",
            domain,
        )
        return False

    params = {"teamId": settings.vercel_team_id} if settings.vercel_team_id else {}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.post(
                f"{_API_BASE}/v10/projects/{project_id}/domains",
                params=params,
                headers={"Authorization": f"Bearer {settings.vercel_api_token}"},
                json={"name": domain},
            )
        if response.status_code in (200, 201):
            log.info("vercel: attached %s to project %s", domain, project_id)
            return True
        # Vercel returns 409 with a specific error code when the domain is
        # already attached to THIS project — not a failure, just a no-op.
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code == 409 and body.get("error", {}).get("code") == "domain_already_in_use":
            log.info("vercel: %s already attached to project %s", domain, project_id)
            return True
        log.warning(
            "vercel: failed to attach %s to project %s: %s %s",
            domain, project_id, response.status_code, body,
        )
        return False
    except httpx.HTTPError as exc:
        log.warning("vercel: request failed attaching %s: %s", domain, exc)
        return False


async def check_domain_connected(domain: str) -> bool | None:
    """Is `domain`'s DNS actually pointed at Vercel right now?

    Calls Vercel's domain CONFIG check (v6/domains/{domain}/config), not the
    project-domains endpoint add_domain_to_project uses — this one reports
    the real-world DNS state (misconfigured or not) regardless of which
    project the domain is attached to, which is what a merchant setting up
    their own DNS actually needs to know: "is it working yet," not "is it
    registered with Vercel."

    Returns None (not False) when the check itself couldn't be answered —
    token missing, network error, etc. — so the caller can show "unknown"
    rather than a false "not connected."
    """
    if not settings.vercel_api_token:
        return None

    params = {"teamId": settings.vercel_team_id} if settings.vercel_team_id else {}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(
                f"{_API_BASE}/v6/domains/{domain}/config",
                params=params,
                headers={"Authorization": f"Bearer {settings.vercel_api_token}"},
            )
        if response.status_code != 200:
            return None
        body = response.json()
        return not bool(body.get("misconfigured", True))
    except httpx.HTTPError as exc:
        log.warning("vercel: domain config check failed for %s: %s", domain, exc)
        return None
