"""Redis cache — fronts the public site-config read path AND the merchant
dashboard's hot reads (analytics, product/order/customer lists).

WHY THIS EXISTS: every visitor to every customer site needs that site's config.
That is the highest-volume query in the system by orders of magnitude, and the
answer changes only when the owner saves an edit. Perfect cache shape:
overwhelmingly read, rarely written, and we know exactly when it changes.

Strategy is write-through invalidation, not timed expiry alone: a save deletes
the key immediately, so edits appear instantly rather than "within 5 minutes".
The TTL is only a safety net for keys we somehow fail to invalidate.

DASHBOARD CACHE: same strategy, coarser invalidation. site_key()/invalidate_site()
above are per-hostname and precise (storefront config). dashboard_key()/
invalidate_dashboard() below are per-site and deliberately blunt — any commerce
write (product/category/order/customer) drops EVERY cached dashboard read for
that site, not just the one it touched, because an order affects analytics,
the orders list, AND the customer it's linked to all at once. Over-invalidating
by a few unrelated keys costs one cheap re-query; under-invalidating shows a
merchant stale numbers right after they made a change. Not a close call.

DEGRADES GRACEFULLY: if Redis is down, every helper here logs and returns as if
it were a cache miss. The site gets slower, not broken. Never let a cache outage
become an outage.
"""

import json
import logging

import redis.asyncio as redis

from app.config import settings

log = logging.getLogger(__name__)

_pool: redis.Redis | None = None


def client() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def site_key(host: str) -> str:
    """Cache key for one site's rendered config, keyed by hostname."""
    return f"site:{host.lower()}"


async def get_json(key: str) -> dict | None:
    if settings.cache_ttl_seconds <= 0:
        return None
    try:
        raw = await client().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 - cache must never break a request
        log.warning("cache read failed for %s: %s", key, exc)
        return None


async def set_json(key: str, value: dict, ttl: int | None = None) -> None:
    if settings.cache_ttl_seconds <= 0:
        return
    try:
        await client().set(
            key, json.dumps(value, default=str),
            ex=ttl or settings.cache_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("cache write failed for %s: %s", key, exc)


async def drop(*keys: str) -> None:
    if not keys:
        return
    try:
        await client().delete(*keys)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache delete failed: %s", exc)


async def drop_prefix(prefix: str) -> None:
    """Delete every key under a prefix.

    Uses SCAN, not KEYS. `KEYS *` blocks the entire Redis server while it walks
    the keyspace — fine with 10 keys, a production incident with 10 million.
    SCAN walks in small batches and lets other commands interleave.
    """
    try:
        c = client()
        async for key in c.scan_iter(match=f"{prefix}*", count=100):
            await c.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache prefix delete failed for %s: %s", prefix, exc)


def dashboard_key(site_id: str, kind: str, suffix: str = "") -> str:
    """Cache key for one merchant-dashboard read, scoped to a site.

    `kind` is the resource ("analytics", "products", "orders", "customers");
    `suffix` folds in whatever query params change the result (weeks, limit,
    offset, status filter, ...) so two different requests never collide.
    """
    return f"dash:{site_id}:{kind}:{suffix}" if suffix else f"dash:{site_id}:{kind}"


async def invalidate_dashboard(site_id: str) -> None:
    """Call after ANY write to a site's products, categories, orders, or
    customers — see the module docstring for why this drops everything for
    the site rather than just the one resource that changed."""
    await drop_prefix(f"dash:{site_id}:")


async def invalidate_site(subdomain: str, custom_domain: str | None = None) -> None:
    """Call after ANY write that changes what a visitor would see.

    A site is reachable under more than one hostname (its subdomain and possibly
    a custom domain), so all of them must be dropped together — otherwise the
    edit appears on one address and not the other, which is a maddening bug to
    chase.
    """
    keys = [site_key(f"{subdomain}.{settings.site_base_domain}"), site_key(subdomain)]
    if custom_domain:
        keys.append(site_key(custom_domain))
    await drop(*keys)
