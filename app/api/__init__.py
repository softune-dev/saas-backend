"""Router aggregation — one import for main.py."""

from fastapi import APIRouter, Depends

from app.api import (
    ai,
    analytics,
    auth,
    commerce,
    courier,
    customers,
    fraud,
    help_desk,
    leads,
    marketing,
    media,
    notifications,
    pages,
    payments,
    public,
    push,
    sites,
    superadmin,
)
from app.security import block_demo_writes

api_router = APIRouter()
api_router.include_router(auth.router)
# auth.router is NOT gated here at the whole-router level — /login and
# /refresh run before any bearer token exists, so block_demo_writes (which
# resolves CurrentUser) would 401 every login attempt, demo or not. Its own
# authenticated writes (PATCH /me, PATCH /tenant, POST /change-password) are
# gated per-route instead — see auth.py. /logout is deliberately left open:
# ending your own session isn't a data write worth blocking for anyone.
#
# public (the unauthenticated storefront API) has no bearer token at all —
# the dependency would 401 every storefront visitor, so it's excluded
# entirely. Every other router below can mutate tenant data somewhere, so
# every one of them gets the demo read-only gate; it only ever acts on
# non-GET requests (see security.py).
_demo_guard = [Depends(block_demo_writes)]
api_router.include_router(analytics.router, dependencies=_demo_guard)
api_router.include_router(sites.router, dependencies=_demo_guard)
api_router.include_router(media.router, dependencies=_demo_guard)
api_router.include_router(pages.router, dependencies=_demo_guard)
api_router.include_router(commerce.router, dependencies=_demo_guard)
api_router.include_router(customers.router, dependencies=_demo_guard)
api_router.include_router(courier.router, dependencies=_demo_guard)
api_router.include_router(payments.router, dependencies=_demo_guard)
api_router.include_router(marketing.router, dependencies=_demo_guard)
api_router.include_router(fraud.router, dependencies=_demo_guard)
api_router.include_router(help_desk.router, dependencies=_demo_guard)
api_router.include_router(notifications.router, dependencies=_demo_guard)
api_router.include_router(push.router, dependencies=_demo_guard)
api_router.include_router(public.router)
api_router.include_router(ai.router, dependencies=_demo_guard)
api_router.include_router(ai.chat_router, dependencies=_demo_guard)
api_router.include_router(ai.actions_router, dependencies=_demo_guard)
api_router.include_router(ai.usage_router, dependencies=_demo_guard)
# NOT gated by _demo_guard — a superadmin's own tenant is never "demo" plan
# and this router's own SuperAdminUser dependency (app/security.py) is a
# stricter gate than block_demo_writes anyway. See superadmin.py's docstring.
api_router.include_router(superadmin.router)
# NOT gated by _demo_guard — leads have no tenant/site at all, block_demo_writes
# (which resolves CurrentUser) would 401 every call. Its own CurrentLead auth
# (app/security.py) is a completely separate, narrower credential. See
# app/api/leads.py's module docstring.
api_router.include_router(leads.router)
