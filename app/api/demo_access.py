"""The public demo's email gate: send a code, then trade it for demo tokens.

Why a code: the demo used to accept any address it was given, and those went
straight into the marketing lists and bounced. A well-formed address like
someone@gmail.com can only be proven by mailing it, so nothing is recorded in
demo_access_requests until the visitor shows they can read that inbox.

The code lives in Redis, not Postgres: it's worthless after ten minutes, and an
address that never confirms should leave no trace. If Redis is down we answer
503 rather than letting visitors through, because skipping the check is exactly
how the fake addresses got in. The demo is optional, so a refusal here costs
little; this is why it doesn't follow the usual "cache failures never fail a
request" rule.

Tokens are read-only regardless of who holds them, enforced by block_demo_writes
(app/security.py). Deliberately decoupled from trial signup (app/api/trial.py),
which is the "actually use it" path.
"""

import json
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, mailer, queue, recaptcha
from app.config import settings
from app.db import get_db
from app.models import DemoAccessRequest, User
from app.ratelimit import _client_ip, demo_access_rate_limit, rate_limit
from app.schemas import DemoAccessIn, DemoVerifyIn, TokenOut
from app.security import generate_otp, hash_otp

log = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])
DB = Annotated[AsyncSession, Depends(get_db)]

_CODE_TTL_SECONDS = 600
_MAX_ATTEMPTS = 5

_UNAVAILABLE = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE,
    "Demo isn't available right now — please contact us instead.",
)


def _code_key(email: str) -> str:
    return f"demo-code:{email}"


def _tries_key(email: str) -> str:
    return f"demo-code-tries:{email}"


async def _demo_user(db: AsyncSession) -> User:
    user = (
        await db.execute(select(User).where(User.email == settings.demo_user_email))
    ).scalar_one_or_none()
    if user is None:
        raise _UNAVAILABLE
    return user


@router.post(
    "/demo-access/start",
    # Per-email stops one inbox being flooded; per-IP stops a script mailing many
    # different strangers. 10 per 10 minutes leaves room for a shared office IP.
    dependencies=[
        Depends(demo_access_rate_limit),
        Depends(rate_limit("demo-start", limit=10, window_seconds=600)),
    ],
)
async def start(payload: DemoAccessIn, request: Request, db: DB) -> dict:
    """Mails a 6-digit code. Asking again replaces the old code."""
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "demo_access", _client_ip(request), payload.recaptcha_v2_token
        )
    )
    await _demo_user(db)  # don't make someone fetch a code for a demo that can't open

    otp = generate_otp()
    try:
        c = cache.client()
        await c.set(_code_key(payload.email), json.dumps({"hash": hash_otp(otp)}), ex=_CODE_TTL_SECONDS)
        await c.delete(_tries_key(payload.email))
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("demo code could not be stored: %s", exc)
        raise _UNAVAILABLE from exc

    # Queued, not awaited inline: a real SMTP send takes several seconds.
    subject, html_body, text_body = mailer.otp_email(otp)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": payload.email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )
    return {"sent": True}


@router.post(
    "/demo-access/verify",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit("demo-verify", limit=20, window_seconds=600))],
)
async def verify(payload: DemoVerifyIn, request: Request, db: DB) -> dict:
    """Checks the code, records the now-proven address, and mints a token pair
    for the shared plan="demo" account. One row per email: demo_access_requests
    is an outreach list, not a click log (see migrations/050)."""
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect or expired code.")
    try:
        c = cache.client()
        raw = await c.get(_code_key(payload.email))
        if raw is None:
            raise invalid
        # INCR rather than a counter inside the JSON: parallel guesses can't
        # all read the same count and slip past the cap.
        tries = await c.incr(_tries_key(payload.email))
        if tries == 1:
            await c.expire(_tries_key(payload.email), _CODE_TTL_SECONDS)
        if tries > _MAX_ATTEMPTS:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — request a new code."
            )
        if not secrets.compare_digest(json.loads(raw)["hash"], hash_otp(payload.otp)):
            raise invalid
        await c.delete(_code_key(payload.email), _tries_key(payload.email))  # single use
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - see module docstring
        log.warning("demo code could not be checked: %s", exc)
        raise _UNAVAILABLE from exc

    demo_user = await _demo_user(db)
    existing = (
        await db.execute(select(DemoAccessRequest).where(DemoAccessRequest.email == payload.email))
    ).scalar_one_or_none()
    ip = _client_ip(request)
    if existing:
        existing.request_count += 1
        existing.last_requested_at = func.now()
        existing.ip = ip
        await crud.save(db, existing)
    else:
        await crud.save(db, DemoAccessRequest(email=payload.email, ip=ip))

    from app.api.auth import _tokens  # local import: avoids a circular import at module load

    return _tokens(demo_user).model_dump()
