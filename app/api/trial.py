"""Self-serve 3-day trial signup — replaces the old lead-capture funnel.

Creates a REAL Tenant + User immediately (plan="trial"), not a staging row
that a superadmin later converts. The only pre-verification state is the
signup itself (email/password/OTP/shop basics) — that lives in Redis
(app/cache.py), keyed by an opaque `signup_token` returned to the client,
TTL settings.trial_signup_ttl_minutes. Abandoned signups just expire; there
is nothing to clean up, unlike the old `leads` table this replaces.

Theme/template choice is NOT sent to the backend until POST /trial/complete
— the frontend wizard holds it in local state only, so browsing themes
never touches the database (see landing's onboarding wizard).

Trial lifetime: POST /trial/complete stamps trial_started_at/trial_expires_at
(trial_days from now) on the Tenant. Login is rejected once that passes
(app/api/auth.py). A background sweep (app/worker.py) hard-deletes the
tenant trial_grace_days after that — see migrations/047_trial_tenants.sql.
"""

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, mailer, queue, recaptcha
from app.config import settings
from app.db import get_db
from app.models import Site, SitePage, User
from app.ratelimit import _client_ip, rate_limit
from app.schemas import (
    TrialCompleteIn,
    TrialDetailsIn,
    TrialResendOtpIn,
    TrialSignupTokenOut,
    TrialStartIn,
    TrialStatusOut,
    TrialVerifyOtpIn,
)
from app.security import generate_otp, hash_otp, hash_password

router = APIRouter(prefix="/trial", tags=["trial"])
DB = Annotated[AsyncSession, Depends(get_db)]

_OTP_TTL_MINUTES = 10
_OTP_MAX_ATTEMPTS = 5


def _redis_key(signup_token: str) -> str:
    return f"trial-signup:{signup_token}"


async def _load_pending(
    signup_token: str, *, not_found_status: int = status.HTTP_400_BAD_REQUEST
) -> dict:
    raw = await cache.client().get(_redis_key(signup_token))
    if raw is None:
        raise HTTPException(
            not_found_status,
            "This signup has expired or wasn't found — please start again.",
        )
    return json.loads(raw)


async def _save_pending(signup_token: str, data: dict) -> None:
    await cache.client().set(
        _redis_key(signup_token),
        json.dumps(data),
        ex=settings.trial_signup_ttl_minutes * 60,
    )


async def _queue_otp_email(to_email: str, otp: str, full_name: str | None) -> None:
    # Queued, not awaited inline — a real SMTP send measured ~6-7 seconds,
    # which made this feel hung behind a "Creating account..." spinner. Same
    # pattern the old leads.py used.
    subject, html_body, text_body = mailer.otp_email(otp, full_name)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": to_email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )


@router.post(
    "/start",
    response_model=TrialSignupTokenOut,
    dependencies=[Depends(rate_limit("trial-start", limit=10, window_seconds=600))],
)
async def start(payload: TrialStartIn, request: Request, db: DB) -> dict:
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "trial_start", _client_ip(request), payload.recaptcha_v2_token
        )
    )

    existing = await db.execute(select(User.id).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account with that email already exists. Try logging in instead.",
        )

    otp = generate_otp()
    signup_token = secrets.token_urlsafe(24)
    await _save_pending(
        signup_token,
        {
            "email": payload.email,
            "password_hash": hash_password(payload.password),
            "full_name": payload.full_name,
            "otp_hash": hash_otp(otp),
            "otp_expires_at": (datetime.now(UTC) + timedelta(minutes=_OTP_TTL_MINUTES)).isoformat(),
            "otp_attempts": 0,
            "email_verified": False,
        },
    )
    await _queue_otp_email(payload.email, otp, payload.full_name)
    return {"signup_token": signup_token, "email_verified": False}


@router.get("/status/{signup_token}", response_model=TrialStatusOut)
async def status_(signup_token: str) -> dict:
    """Resume check — called on the wizard's mount so someone who verified
    their email, then closed the tab (e.g. to go check their inbox for the
    OTP in the first place) and came back, lands back on the step they left
    instead of redoing Account+OTP from scratch. 404 means the signup
    expired or never existed — the frontend's cue to clear its stored
    signup_token and start clean, not a broken state to recover from."""
    pending = await _load_pending(signup_token, not_found_status=status.HTTP_404_NOT_FOUND)
    return {
        "signup_token": signup_token,
        "email": pending["email"],
        "full_name": pending.get("full_name"),
        "email_verified": pending["email_verified"],
        "shop_name": pending.get("shop_name"),
        "phone": pending.get("phone"),
        "tagline": pending.get("tagline"),
        "category": pending.get("category"),
    }


@router.post(
    "/resend-otp",
    response_model=TrialSignupTokenOut,
    dependencies=[Depends(rate_limit("trial-resend-otp", limit=5, window_seconds=600))],
)
async def resend_otp(payload: TrialResendOtpIn) -> dict:
    pending = await _load_pending(payload.signup_token)
    if pending["email_verified"]:
        return {"signup_token": payload.signup_token, "email_verified": True}

    otp = generate_otp()
    pending["otp_hash"] = hash_otp(otp)
    pending["otp_expires_at"] = (datetime.now(UTC) + timedelta(minutes=_OTP_TTL_MINUTES)).isoformat()
    pending["otp_attempts"] = 0
    await _save_pending(payload.signup_token, pending)

    await _queue_otp_email(pending["email"], otp, pending["full_name"])
    return {"signup_token": payload.signup_token, "email_verified": False}


@router.post(
    "/verify-otp",
    response_model=TrialSignupTokenOut,
    dependencies=[Depends(rate_limit("trial-verify-otp", limit=10, window_seconds=600))],
)
async def verify_otp(payload: TrialVerifyOtpIn) -> dict:
    pending = await _load_pending(payload.signup_token)
    if pending["email_verified"]:
        return {"signup_token": payload.signup_token, "email_verified": True}

    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect or expired code.")

    if pending["otp_attempts"] >= _OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — request a new code."
        )
    pending["otp_attempts"] += 1

    otp_expires_at = datetime.fromisoformat(pending["otp_expires_at"])
    if datetime.now(UTC) > otp_expires_at or not secrets.compare_digest(
        pending["otp_hash"], hash_otp(payload.otp)
    ):
        await _save_pending(payload.signup_token, pending)
        raise invalid

    pending["email_verified"] = True
    pending["otp_hash"] = None
    pending["otp_expires_at"] = None
    await _save_pending(payload.signup_token, pending)
    return {"signup_token": payload.signup_token, "email_verified": True}


@router.patch("/details", response_model=TrialSignupTokenOut)
async def update_details(payload: TrialDetailsIn) -> dict:
    pending = await _load_pending(payload.signup_token)
    if not pending["email_verified"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify your email first.")

    pending["shop_name"] = payload.shop_name
    pending["phone"] = payload.phone
    pending["tagline"] = payload.tagline
    pending["category"] = payload.category
    await _save_pending(payload.signup_token, pending)
    return {"signup_token": payload.signup_token, "email_verified": True}


@router.post(
    "/complete",
    dependencies=[Depends(rate_limit("trial-complete", limit=5, window_seconds=600))],
)
async def complete(payload: TrialCompleteIn, db: DB) -> dict:
    pending = await _load_pending(payload.signup_token)
    if not pending["email_verified"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify your email first.")
    if not pending.get("shop_name"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Shop details are required first.")

    # Unique subdomain from the shop name — same increment-on-collision
    # pattern crud.create_tenant_owner_and_site already uses for the
    # workspace slug, done here too since Site.subdomain has its own unique
    # constraint that function doesn't otherwise guard against.
    base = crud.slugify(pending["shop_name"], "shop")
    subdomain = base
    for n in range(2, 100):
        taken = await db.execute(select(Site.id).where(Site.subdomain == subdomain))
        if not taken.scalar_one_or_none():
            break
        subdomain = f"{base}-{n}"

    # siteName/tagline live in Site.theme, NOT Site.business — that's what
    # the dashboard's own Setup -> Shop identity step (and the theme
    # editor's Brand panel) actually render (see
    # dashboard/components/onboarding/steps/step-shop-basics.tsx's
    # `s.siteName`/`s.tagline`, bound to draftSettings which mirrors
    # Site.theme). Writing these into Site.business instead was a real bug
    # — the collected shop name/tagline silently never showed up anywhere.
    theme_overrides = {"siteName": pending["shop_name"]}
    if pending.get("tagline"):
        theme_overrides["tagline"] = pending["tagline"]
    if payload.theme.primary_color:
        theme_overrides["primaryColor"] = payload.theme.primary_color
    # displayFont/bodyFont, NOT "font" — that's the actual field name the
    # theme editor and onboarding's Colors-and-fonts step read (see
    # dashboard/components/onboarding/steps/step-brand.tsx's
    # `s.displayFont`/`s.bodyFont`). Writing to "font" was a real bug: the
    # heading font chosen during signup silently never appeared anywhere
    # past the wizard itself, and the body font wasn't even sent.
    if payload.theme.font:
        theme_overrides["displayFont"] = payload.theme.font
    if payload.theme.body_font:
        theme_overrides["bodyFont"] = payload.theme.body_font

    # Phone genuinely belongs on Site.business (customer-facing contact
    # info — Site Settings -> Contact reads it from here), separate from
    # Tenant.business (legal/tax identity, unrelated). `type` is the same
    # field the dashboard's own onboarding writes for its "Shop category /
    # niche" question (step-shop-basics.tsx) — reusing it here means a
    # trial that converts to a paid plan already has it set, nothing to
    # re-ask.
    business_overrides = {}
    if pending.get("phone"):
        business_overrides["phone"] = pending["phone"]
    if pending.get("category"):
        business_overrides["type"] = pending["category"]

    user, site = await crud.create_tenant_owner_and_site(
        db,
        email=pending["email"],
        password_hash=pending["password_hash"],
        full_name=pending.get("full_name"),
        phone=pending.get("phone"),
        workspace_name=pending["shop_name"],
        plan="trial",
        template_key=payload.template_key,
        site_name=pending["shop_name"],
        subdomain=subdomain,
        trial_days=settings.trial_days,
        site_theme_overrides=theme_overrides,
        site_business_overrides=business_overrides or None,
    )

    # Auto-publish — a trial merchant should see a live site the moment
    # they land in the dashboard, not have to find and click Publish first
    # with nothing but a template default in it. Same steps
    # POST /sites/{id}/publish takes (app/api/sites.py), minus the
    # publish-cooldown check (this site is seconds old) and the "you just
    # published" notification (nothing to tell them about their own signup).
    pages = (
        await db.execute(select(SitePage).where(SitePage.site_id == site.id))
    ).scalars().all()
    for page in pages:
        page.is_published = True
    site.status = "published"
    site.published_at = datetime.now(UTC)
    site.seo = {**(site.seo or {}), "noindex": False}
    await db.commit()

    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site.id), "paths": ["/"]})
    await queue.publish(queue.JOB_GENERATE_SITEMAP, {"site_id": str(site.id)})
    await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site.id)})
    await queue.publish(queue.JOB_CAPTURE_SCREENSHOT, {"site_id": str(site.id)})

    await cache.drop(_redis_key(payload.signup_token))

    # No invoice for the trial itself — a trial is free, so there's nothing
    # to bill or document. Invoices now only start once superadmin sets a
    # real paid plan (see app/api/superadmin.py's update_tenant).

    subject, html_body, text_body = mailer.welcome_email(user.full_name)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": user.email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )

    from app.api.auth import _tokens  # local import: avoids a circular import at module load

    return _tokens(user).model_dump()
