"""Application entry point.

Run it with:   uvicorn app.main:app --reload
Interactive docs:   http://localhost:8000/docs
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import cache, queue
from app.api import api_router
from app.config import settings
from app.db import engine

logging.basicConfig(
    level=logging.INFO if settings.debug else logging.WARNING,
    format="%(levelname)-8s %(name)s | %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup and shutdown.

    Startup deliberately does NOT crash when Redis or RabbitMQ are unreachable —
    it warns instead. You should be able to work on API and database code without
    Docker running. Postgres is different: without it nothing works, so a failure
    there is worth shouting about (but still not a hard exit, so you can read the
    error at /health instead of a stack trace at boot).
    """
    log.info("starting in %s mode", settings.app_env)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("database: connected")
    except Exception as exc:  # noqa: BLE001
        log.error("database: NOT connected -> %s", exc)
        log.error("  check DATABASE_URL in .env (see docs/TROUBLESHOOTING.md)")

    try:
        await cache.client().ping()
        log.info("redis: connected")
    except Exception:  # noqa: BLE001
        log.warning("redis: not available - caching disabled (run: docker compose up -d)")

    try:
        await queue.connect()
        log.info("rabbitmq: connected")
    except Exception:  # noqa: BLE001
        log.warning("rabbitmq: not available - jobs will be skipped")

    yield

    await cache.close()
    await queue.close()
    await engine.dispose()
    log.info("shutdown complete")


app = FastAPI(
    title="SaaS Site Builder API",
    version="0.1.0",
    description=(
        "Multi-tenant backend for template-based site building.\n\n"
        "**Start here:** POST /auth/register, copy the access_token, click "
        "Authorize above, then POST /sites."
    ),
    lifespan=lifespan,
    # Hide interactive docs in production — they map your entire attack surface.
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def public_cors(request: Request, call_next):
    """CORS_ORIGINS is a fixed allowlist for the dashboard's own known
    origin(s) — right for authenticated endpoints, but wrong for /public/*.
    Every merchant's storefront lives on a DIFFERENT domain we can't know in
    advance (that's the whole point of custom/subdomains), so a fixed
    allowlist would mean manually adding every future customer's domain
    here just to let their own checkout work. /public/* is unauthenticated
    and returns only public data, so reflecting whatever Origin asked is
    safe — there's nothing origin-restriction would protect here that
    CORSMiddleware's allow_origins already protects for every other route.

    Runs as its own middleware, not a CORSMiddleware allow_origins entry,
    because that setting applies to the WHOLE app — there's no per-route
    option on a single CORSMiddleware instance.
    """
    if not request.url.path.startswith("/public/"):
        return await call_next(request)

    origin = request.headers.get("origin", "*")
    if request.method == "OPTIONS":
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "600",
            },
        )
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    return response


@app.middleware("http")
async def ip_block(request: Request, call_next):
    """Site-wide IP blocking — Settings -> Fraud Protection's IP blocklist
    (app/api/fraud.py, dashboard/components/fraud/). Unlike the phone
    blocklist (checked only at checkout submission), this blocks EVERY
    /public/* request for a site, so a blocked visitor can't browse the
    storefront at all, not just fail at checkout.

    host is read from the URL PATH (/public/site/{host}/...), never the
    Host header — the API is on its own domain, the storefront's own domain
    only ever appears in the path here (see _find_published_site's identical
    reasoning in app/api/public.py).

    Blocked requests get an explicit, structured 403 (code "ip_blocked") —
    merchant's own product choice: tell a blocked visitor plainly, with a
    way to reach support, rather than disguising it as a generic 404. Sets
    its own CORS headers on that response rather than relying on
    public_cors to add them, so a blocked storefront gets a clean response
    its own JS can actually read, not a CORS failure that just looks broken.

    IMPORTANT — this only sees the real visitor IP for requests that
    actually carry it. A server-rendered page's OWN data fetch (Next.js
    Server Component calling this API from the storefront's own backend
    process) does NOT automatically forward the browser's IP — the
    storefront must explicitly thread it through as X-Forwarded-For on that
    outbound call (see templates/*/middleware.ts + lib/get-site.ts) or a
    blocked visitor can still load pages that render server-side, even
    though direct client-side calls (checkout, etc.) are correctly blocked.

    Fails OPEN on any Redis/DB error — same discipline as
    app/ratelimit.py's rate_limit — a Redis blip must never take every
    storefront down at once.
    """
    if not request.url.path.startswith("/public/"):
        return await call_next(request)

    parts = request.url.path.split("/")
    # ["", "public", "site", "{host}", ...] — anything shorter isn't a
    # /public/site/{host}/... request (e.g. /public/openapi.json, if any),
    # so just let it through unchecked rather than guessing.
    if len(parts) < 4 or parts[2] != "site" or not parts[3]:
        return await call_next(request)
    host = parts[3]

    blocked_ip: str | None = None
    try:
        cached = await cache.get_json(cache.ip_block_key(host))
        if cached is None:
            from sqlalchemy import or_, select

            from app.db import SessionLocal
            from app.models import FraudIpBlocklistEntry, Site

            async with SessionLocal() as session:
                site_row = (
                    await session.execute(
                        select(Site.id).where(
                            or_(Site.custom_domain == host, Site.subdomain == host.split(".")[0]),
                            Site.status == "published",
                        )
                    )
                ).scalar_one_or_none()
                ips: list[str] = []
                if site_row is not None:
                    ips = (
                        await session.execute(
                            select(FraudIpBlocklistEntry.ip_address).where(
                                FraudIpBlocklistEntry.site_id == site_row
                            )
                        )
                    ).scalars().all()
            cached = {"ips": [str(ip) for ip in ips]}
            # Short TTL, deliberately shorter than the site-config cache — a
            # merchant who just got attacked needs a freshly-added block to
            # take effect fast, not wait out a longer TTL. Cached even when
            # empty (the overwhelming default case) so a site with zero IP
            # blocks doesn't hit Postgres on every single public request.
            await cache.set_json(cache.ip_block_key(host), cached, ttl=60)

        from app.ratelimit import _client_ip

        request_ip = _client_ip(request)
        if request_ip in set(cached.get("ips") or []):
            blocked_ip = request_ip
    except Exception as exc:  # noqa: BLE001 - fail open, never break a request
        log.warning("ip_block check failed for host=%s: %s", host, exc)

    if blocked_ip:
        # Explicit, structured signal — merchant's own choice: tell a
        # blocked visitor plainly they're blocked, with a way to reach
        # support, rather than disguising it as a generic 404. `code` is
        # machine-readable, same convention as recaptcha's
        # "recaptcha_challenge_required" (see app/recaptcha.py's enforce),
        # so the storefront can detect this specific case and render a real
        # branded page instead of a raw error string.
        origin = request.headers.get("origin", "*")
        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "ip_blocked",
                    "message": "Your IP address has been blocked by this store.",
                }
            },
            headers={"Access-Control-Allow-Origin": origin, "Vary": "Origin"},
        )

    return await call_next(request)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    """Add X-Response-Time-ms to every response.

    Kept because you asked for a fast API: this makes slowness visible in Postman
    immediately, rather than something you only notice once customers complain.
    Watch it drop from ~200ms to ~2ms on the second call to a cached endpoint.
    """
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-ms"] = f"{elapsed:.1f}"
    if elapsed > 500:
        log.warning("SLOW %s %s took %.0fms", request.method, request.url.path, elapsed)
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Last-resort handler: log the full detail, return a generic message.

    Stack traces and database errors must never reach a client — they disclose
    table names, query shapes and library versions. The full traceback still goes
    to your terminal.
    """
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    detail = str(exc) if settings.is_dev else "Internal server error"
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Dependency check. Hit this FIRST when something seems wrong — it tells you
    which of the three moving parts is unhappy."""
    result: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["database"] = f"error: {type(exc).__name__}"

    try:
        await cache.client().ping()
        result["redis"] = "ok"
    except Exception:  # noqa: BLE001
        result["redis"] = "unavailable"

    try:
        channel = await queue.connect()
        result["rabbitmq"] = "ok" if not channel.is_closed else "closed"
    except Exception:  # noqa: BLE001
        result["rabbitmq"] = "unavailable"

    result["status"] = "ok" if result["database"] == "ok" else "degraded"
    return result


app.include_router(api_router)
