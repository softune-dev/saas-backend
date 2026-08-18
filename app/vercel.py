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
    """Attach `domain` to the given Vercel project. Returns True on success.

    A 409 "domain_already_in_use" is ambiguous by status code alone —
    Vercel returns it BOTH when the domain is already attached to THIS
    exact project (a harmless no-op, should count as success) and when
    it's attached to a genuinely DIFFERENT project (a real conflict).
    The response body's error.domain.projectId says which one it is —
    that's what decides the outcome here, not the status code on its own.

    (This distinction used to be missing entirely in both directions at
    different times: first ALL 409s were treated as success, silently
    masking real cross-project conflicts; then, overcorrecting, ALL 409s
    were treated as failure, which broke the actually-harmless same-project
    case and spammed false failures into the logs. Checking the actual
    project id in the error body is the real fix.)
    """
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

        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code == 409:
            error = body.get("error", {})
            existing_project = error.get("domain", {}).get("projectId") or error.get("projectId")
            if error.get("code") == "domain_already_in_use" and existing_project == project_id:
                log.info("vercel: %s already attached to project %s (no-op)", domain, project_id)
                return True

        log.warning(
            "vercel: failed to attach %s to project %s: %s %s",
            domain, project_id, response.status_code, body,
        )
        return False
    except httpx.HTTPError as exc:
        log.warning("vercel: request failed attaching %s: %s", domain, exc)
        return False


async def remove_domain_from_project(domain: str, project_id: str) -> bool:
    """Detach `domain` from the given Vercel project — the mirror of
    add_domain_to_project. Without this, "Remove" in the dashboard would
    only clear our own database field while the domain kept silently
    serving that site's storefront on Vercel's side, since nothing else
    would ever tell Vercel to let it go. Returns True on success, including
    "already not attached" (404) — idempotent, same reasoning as add."""
    if not settings.vercel_api_token or not project_id:
        log.info(
            "vercel: skipping domain detach for %s (token or project id not configured)",
            domain,
        )
        return False

    params = {"teamId": settings.vercel_team_id} if settings.vercel_team_id else {}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.delete(
                f"{_API_BASE}/v9/projects/{project_id}/domains/{domain}",
                params=params,
                headers={"Authorization": f"Bearer {settings.vercel_api_token}"},
            )
        if response.status_code in (200, 204, 404):
            log.info("vercel: detached %s from project %s", domain, project_id)
            return True
        log.warning(
            "vercel: failed to detach %s from project %s: %s",
            domain, project_id, response.status_code,
        )
        return False
    except httpx.HTTPError as exc:
        log.warning("vercel: request failed detaching %s: %s", domain, exc)
        return False


async def check_domain_connected(domain: str, project_id: str) -> bool | None:
    """Is `domain` actually serving THIS site right now?

    Two things both have to be true, and checking only the first one is
    exactly the bug that shipped initially: DNS can point at Vercel
    generically (misconfigured=false) while the domain isn't attached to
    THIS project at all — Vercel then serves a 404 DEPLOYMENT_NOT_FOUND
    page instead of the site, which a DNS-only check reports as "Connected"
    anyway. Confirmed this exact failure mode for real: a domain still
    claimed by a different project passed the DNS check while 404ing in
    the browser.

    So this checks project attachment first (v9/projects/{id}/domains/{d}
    — 404 there means not attached to THIS project, a real "not
    connected", not a caching artifact) and DNS config second — both must
    be positive to report true.

    Returns None (not False) when the check itself couldn't be answered —
    token missing, network error, etc. — so the caller can show "unknown"
    rather than a false "not connected."
    """
    if not settings.vercel_api_token or not project_id:
        return None

    params = {"teamId": settings.vercel_team_id} if settings.vercel_team_id else {}
    headers = {"Authorization": f"Bearer {settings.vercel_api_token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            attachment = await http.get(
                f"{_API_BASE}/v9/projects/{project_id}/domains/{domain}",
                params=params,
                headers=headers,
            )
            if attachment.status_code == 404:
                return False
            if attachment.status_code != 200:
                return None

            config = await http.get(
                f"{_API_BASE}/v6/domains/{domain}/config",
                params=params,
                headers=headers,
            )
        if config.status_code != 200:
            return None
        return not bool(config.json().get("misconfigured", True))
    except httpx.HTTPError as exc:
        log.warning("vercel: domain connection check failed for %s: %s", domain, exc)
        return None
