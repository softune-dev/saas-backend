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


async def demo_access_rate_limit(request: Request) -> None:
    """Demo access has no per-visitor friction by design — anyone can request
    it, repeatedly, from anywhere; the endpoint itself upserts by email
    instead of erroring on a repeat. The only cap is per-email (5/day),
    purely to stop one address from being hammered/scripted; there's
    deliberately no IP window, since a shared office/campus IP legitimately
    generates many distinct real signups.

    Reads the email from the request body directly, same trick
    login_rate_limit uses — Starlette caches the raw body, so this doesn't
    interfere with the route's own `payload: DemoAccessIn` parsing after.
    """
    email = ""
    try:
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
    except Exception:  # noqa: BLE001 - malformed body; the route's own validation rejects it
        pass

    if not email:
        return
    checks = [(f"ratelimit:demo_email:{email}", 5, 86400)]

    try:
        c = cache.client()
        for redis_key, limit, window_seconds in checks:
            count = await c.incr(redis_key)
            if count == 1:
                await c.expire(redis_key, window_seconds)
            if count > limit:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many demo requests. Please try again later.",
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("demo access rate limit check failed for %s: %s (allowing request)", ip, exc)
        return


async def login_rate_limit(request: Request) -> None:
    """Two independent windows, both enforced: per-IP (catches a single
    attacker spraying many emails) and per-email (catches a distributed
    brute force against one account from many IPs — a plain per-IP limit
    alone lets that straight through). The per-IP limit is deliberately
    generous (20/5min) since it's shared by everyone behind the same NAT/
    office connection; the per-email limit is tighter (8/5min) since a real
    user mistyping their password a handful of times is normal, dozens
    isn't.

    Reads the email from the request body directly rather than declaring it
    as a route parameter — Starlette caches the raw body after the first
    read, so this doesn't interfere with FastAPI's own `payload: LoginIn`
    parsing in the route handler afterward.
    """
    ip = _client_ip(request)
    email = ""
    try:
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
    except Exception:  # noqa: BLE001 - malformed body; let the route's own validation reject it
        pass

    checks = [(f"ratelimit:login_ip:{ip}", 20, 300)]
    if email:
        checks.append((f"ratelimit:login_email:{email}", 8, 300))

    try:
        c = cache.client()
        for redis_key, limit, window_seconds in checks:
            count = await c.incr(redis_key)
            if count == 1:
                await c.expire(redis_key, window_seconds)
            if count > limit:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many login attempts. Please try again in a few minutes.",
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("login rate limit check failed for %s: %s (allowing request)", ip, exc)
        return
