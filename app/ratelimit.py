"""Fixed-window rate limiting for public (unauthenticated) endpoints —
checkout and the contact form, specifically. Neither has an OTP or any other
identity check, so without this a script can place unlimited fake orders or
flood a merchant's inbox at zero cost.

Backed by the same Redis instance as cache.py (INCR + EXPIRE — the standard
fixed-window counter pattern: cheap, no extra moving parts).

FAILS OPEN: if Redis is unreachable, requests are allowed through rather than
taking the storefront down — same tradeoff cache.py and queue.py already
make elsewhere. A rate limiter that is briefly disabled is far better than a
Redis blip turning into "nobody can check out."
"""

import logging

from fastapi import HTTPException, Request, status

from app import cache

log = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    # Behind a proxy (Vercel, nginx, etc.) the real client IP is the first
    # entry in X-Forwarded-For; request.client.host would just be the proxy.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(key: str, limit: int, window_seconds: int):
    """FastAPI dependency factory: at most `limit` requests per client IP per
    `window_seconds`, in a bucket named `key` (one counter per endpoint, so
    hammering checkout doesn't also throttle the contact form).

    Usage: `Depends(rate_limit("checkout", limit=5, window_seconds=300))`.
    """

    async def _dependency(request: Request) -> None:
        redis_key = f"ratelimit:{key}:{_client_ip(request)}"
        try:
            c = cache.client()
            count = await c.incr(redis_key)
            if count == 1:
                await c.expire(redis_key, window_seconds)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            log.warning("rate limit check failed for %s: %s (allowing request)", redis_key, exc)
            return

        if count > limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests. Please try again in a few minutes.",
            )

    return _dependency
