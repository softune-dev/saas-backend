"""Google reCAPTCHA verification: v3 (invisible, score-based) as the primary
check, with v2 (checkbox challenge) as a fallback when v3's score is too low
to auto-approve but not so clearly a bot that a real human should be locked
out with no recourse.

Used on every publicly-reachable, unauthenticated write a bot could hit:
/auth/login (dashboard + the account switcher's "Add account" modal, which
calls the same endpoint), and the storefront's contact form and checkout
(app/api/public.py).

WHY A FALLBACK AT ALL: v3 has no interactive challenge — a real person on a
VPN, a shared office IP, or an unusual browser setup can score low despite
being completely legitimate, and would otherwise be flatly rejected with no
way to prove themselves. v2's checkbox gives them that recourse. A token
Google says is outright invalid (wrong action, expired, malformed) skips the
fallback entirely — that is a bot signal, not a "please verify differently"
case.

Three verify() outcomes:
  - OK: proceed normally.
  - CHALLENGE: v3 was valid but scored low, and a v2 secret key IS configured
    -> caller should ask the frontend to render the v2 checkbox and retry
    with a v2 token, rather than reject outright.
  - Anything else -> reject (raises inside verify() via the caller's own
    exception; verify() itself just returns a bool + optional challenge flag,
    see VerifyResult below).

Fails OPEN only on Google being unreachable (network error, timeout,
outage) — an infrastructure problem, not a bot signal. A reCAPTCHA outage
should never take down login, checkout, or the contact form. Skips
verification entirely when RECAPTCHA_SECRET_KEY is unset (local dev, or
before it's configured in prod).
"""

import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, status

from app.config import settings

log = logging.getLogger(__name__)

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

# Google's own guidance: 0.5 is a reasonable default threshold, adjust after
# watching real traffic. Below this, a request is more likely bot than not.
MIN_SCORE = 0.5


@dataclass
class VerifyResult:
    ok: bool
    # True only when v3 scored low AND a v2 fallback is actually available —
    # the caller uses this to decide whether to ask for a v2 token instead
    # of rejecting outright.
    needs_v2_challenge: bool = False


async def _post_siteverify(secret: str, token: str, remote_ip: str | None) -> dict | None:
    """None means Google was unreachable — the caller treats that as
    fail-open, never as a rejection."""
    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(VERIFY_URL, data=data)
        res.raise_for_status()
        return res.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("reCAPTCHA verify unreachable, allowing request: %s", exc)
        return None


async def verify(
    v3_token: str,
    action: str,
    remote_ip: str | None = None,
    v2_token: str = "",
) -> VerifyResult:
    """Primary v3 check, with a v2 fallback path. See module docstring."""
    if not settings.recaptcha_secret_key:
        return VerifyResult(ok=True)

    # A v2 token present means the frontend already went through the
    # fallback challenge for THIS submission — verify that instead of v3.
    # v2's siteverify response has no score/action, just success.
    if v2_token:
        if not settings.recaptcha_v2_secret_key:
            return VerifyResult(ok=False)
        body = await _post_siteverify(settings.recaptcha_v2_secret_key, v2_token, remote_ip)
        if body is None:
            return VerifyResult(ok=True)  # Google unreachable — fail open
        return VerifyResult(ok=bool(body.get("success")))

    if not v3_token:
        # A configured secret key with no token at all is the one case
        # that's unambiguously not Google's fault — always reject.
        return VerifyResult(ok=False)

    body = await _post_siteverify(settings.recaptcha_secret_key, v3_token, remote_ip)
    if body is None:
        return VerifyResult(ok=True)  # Google unreachable — fail open

    if not body.get("success"):
        return VerifyResult(ok=False)
    if body.get("action") != action:
        log.warning("reCAPTCHA action mismatch: expected %s, got %s", action, body.get("action"))
        return VerifyResult(ok=False)
    if float(body.get("score", 0)) >= MIN_SCORE:
        return VerifyResult(ok=True)

    # Valid token, just a low score — offer the v2 fallback if it's set up;
    # otherwise this is still a straight rejection (same as before the
    # fallback existed).
    return VerifyResult(ok=False, needs_v2_challenge=bool(settings.recaptcha_v2_secret_key))


def enforce(result: VerifyResult) -> None:
    """Raises the right HTTPException for a failed verify() result — one
    call site so every endpoint (login, contact, checkout) produces the same
    shape. `needs_v2_challenge` uses a dict `detail` with a machine-readable
    `code` the frontend checks for (see dashboard's lib/api.ts and each
    template's lib/recaptcha.ts) to know to render the v2 checkbox and retry,
    instead of just showing a dead-end error."""
    if result.ok:
        return
    if result.needs_v2_challenge:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "recaptcha_challenge_required",
                "message": "Please complete the extra verification below and try again.",
            },
        )
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Captcha verification failed — please try again.")
