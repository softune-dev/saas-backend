"""Lead capture funnel — signup, OTP, basics, demo access, purchase request.

A lead is NOT a tenant/user. Public self-signup for a REAL account is still
closed (see app/api/auth.py's own docstring on that) — this exists
alongside it, for prospects who haven't paid yet. Nothing here ever
provisions a real tenant automatically; that's still the superadmin's own
POST /superadmin/tenants, a deliberate human step after a sales
conversation, not something a lead triggers by clicking through this funnel.

Auth model: a lead_token (see app/security.py's create_lead_token/CurrentLead)
issued at signup carries a lead through every step. It is NOT a real access
token and is rejected everywhere outside this router. POST /leads/login
exists purely to reissue one for someone whose original token expired or
got cleared — it is NOT how a lead first gets into the funnel (that's
POST /leads/signup); it's the recovery path back in.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, mailer, queue, recaptcha
from app.config import settings
from app.db import get_db
from app.models import Lead, User
from app.ratelimit import _client_ip, login_rate_limit, rate_limit
from app.schemas import (
    LeadDemoAccessOut,
    LeadLoginIn,
    LeadMeOut,
    LeadOtpVerifyIn,
    LeadProfileUpdate,
    LeadPurchaseRequestIn,
    LeadPurchaseRequestOut,
    LeadSignupIn,
    LeadTokenOut,
)
from app.security import (
    CurrentLead,
    create_access_token,
    create_lead_token,
    generate_otp,
    hash_otp,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/leads", tags=["leads"])
DB = Annotated[AsyncSession, Depends(get_db)]

_OTP_TTL_MINUTES = 10
_OTP_MAX_ATTEMPTS = 5

# Allowlist, not a blocklist — new disposable/temp-mail domains appear
# constantly, so trying to block them by name is a losing, endless game.
# Only accepting known major providers is immune to that by construction:
# anything not recognized is rejected, whether it's a brand-new temp-mail
# service or one nobody's heard of yet. Deliberately does NOT include
# custom/business domains (e.g. "you@yourshop.com") — a real prospect
# signing up before they have a business email is the expected case here,
# not the exception.
_ALLOWED_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "protonmail.com", "proton.me",
    "zoho.com",
    "gmx.com",
}


def _has_allowed_email_domain(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in _ALLOWED_EMAIL_DOMAINS


async def _queue_otp_email(to_email: str, otp: str, full_name: str | None) -> None:
    """Queued, not awaited inline — a real SMTP send to Hostinger measured
    ~6-7 seconds end to end (TLS handshake + auth + send), which made
    signup/resend-otp feel hung behind a "Creating account..." spinner.
    Same fire-and-forget pattern as JOB_SEND_ORDER_NOTIFICATIONS elsewhere
    in this codebase — see app/worker.py's handle_send_email."""
    subject, html_body, text_body = mailer.otp_email(otp, full_name)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": to_email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )


@router.post(
    "/login",
    response_model=LeadTokenOut,
    dependencies=[Depends(login_rate_limit)],
)
async def login(payload: LeadLoginIn, request: Request, db: DB) -> dict:
    """Recovery path back into the funnel — see this module's own docstring
    for why this exists alongside, not instead of, /leads/signup."""
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "lead_login", _client_ip(request), payload.recaptcha_v2_token
        )
    )

    lead = (await db.execute(select(Lead).where(Lead.email == payload.email))).scalar_one_or_none()

    # Same "one message for every failure mode" reasoning as /auth/login —
    # distinguishing "no such lead" from "wrong password" turns a
    # password-guessing problem into a list-building one.
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if lead is None or not verify_password(payload.password, lead.password_hash):
        raise invalid

    return {"lead_token": create_lead_token(lead.id), "status": lead.status}


@router.get("/me", response_model=LeadMeOut)
async def me(lead_id: CurrentLead, db: DB) -> Lead:
    """Called with a stored lead_token on page load to figure out where to
    resume the funnel — see LeadMeOut's docstring."""
    return await _get_lead(lead_id, db)


async def _get_lead(lead_id: uuid.UUID, db: AsyncSession) -> Lead:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return lead


@router.post(
    "/signup",
    response_model=LeadTokenOut,
    dependencies=[Depends(rate_limit("lead-signup", limit=10, window_seconds=600))],
)
async def signup(payload: LeadSignupIn, request: Request, db: DB) -> dict:
    """Creates (or reuses, if they abandoned an earlier attempt) a lead row
    and sends a fresh OTP. Re-signing up with the same email before
    verifying just resets the OTP rather than 409ing — a prospect who
    fat-fingered their email the first time, or whose OTP email never
    arrived, needs to be able to just try again."""
    recaptcha.enforce(
        await recaptcha.verify(
            payload.recaptcha_token, "lead_signup", _client_ip(request), payload.recaptcha_v2_token
        )
    )

    if not _has_allowed_email_domain(payload.email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Please sign up with a Gmail, Outlook/Hotmail, Yahoo, iCloud, or "
            "other major email provider.",
        )

    existing = (
        await db.execute(select(Lead).where(Lead.email == payload.email))
    ).scalar_one_or_none()

    if existing and existing.status != "signed_up":
        # Already verified at least once before — don't let signup silently
        # reset a further-along lead back to square one and overwrite their
        # password with whatever they just typed by mistake.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This email has already been verified. Continue where you left off, or contact us.",
        )

    otp = generate_otp()
    otp_hash = hash_otp(otp)
    otp_expires_at = datetime.now(UTC) + timedelta(minutes=_OTP_TTL_MINUTES)

    if existing:
        existing.password_hash = hash_password(payload.password)
        existing.full_name = payload.full_name or existing.full_name
        existing.otp_hash = otp_hash
        existing.otp_expires_at = otp_expires_at
        existing.otp_attempts = 0
        lead = await crud.save(db, existing)
    else:
        lead = Lead(
            email=payload.email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            otp_hash=otp_hash,
            otp_expires_at=otp_expires_at,
        )
        lead = await crud.save(db, lead)

    await _queue_otp_email(lead.email, otp, lead.full_name)
    return {"lead_token": create_lead_token(lead.id), "status": lead.status}


@router.post(
    "/resend-otp",
    response_model=LeadTokenOut,
    dependencies=[Depends(rate_limit("lead-resend-otp", limit=5, window_seconds=600))],
)
async def resend_otp(lead_id: CurrentLead, db: DB) -> dict:
    lead = await _get_lead(lead_id, db)
    if lead.status != "signed_up":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This email is already verified.")

    otp = generate_otp()
    lead.otp_hash = hash_otp(otp)
    lead.otp_expires_at = datetime.now(UTC) + timedelta(minutes=_OTP_TTL_MINUTES)
    lead.otp_attempts = 0
    lead = await crud.save(db, lead)

    await _queue_otp_email(lead.email, otp, lead.full_name)
    return {"lead_token": create_lead_token(lead.id), "status": lead.status}


@router.post(
    "/verify-otp",
    response_model=LeadTokenOut,
    dependencies=[Depends(rate_limit("lead-verify-otp", limit=10, window_seconds=600))],
)
async def verify_otp(payload: LeadOtpVerifyIn, lead_id: CurrentLead, db: DB) -> dict:
    lead = await _get_lead(lead_id, db)
    if lead.status != "signed_up":
        return {"lead_token": create_lead_token(lead.id), "status": lead.status}

    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect or expired code.")

    if lead.otp_attempts >= _OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — request a new code."
        )
    lead.otp_attempts += 1

    if (
        not lead.otp_hash
        or not lead.otp_expires_at
        or datetime.now(UTC) > lead.otp_expires_at
        or not secrets.compare_digest(lead.otp_hash, hash_otp(payload.otp))
    ):
        await crud.save(db, lead)
        raise invalid

    lead.status = "otp_verified"
    lead.otp_hash = None
    lead.otp_expires_at = None
    lead = await crud.save(db, lead)

    # Marketing nudge, not the OTP email — fires once, right here, since this
    # is the first point a lead is a real confirmed email, not a raw signup
    # that might be a typo or never gets verified at all.
    subject, html_body, text_body = mailer.welcome_email(lead.full_name)
    await queue.publish(
        queue.JOB_SEND_EMAIL,
        {"to": lead.email, "subject": subject, "html_body": html_body, "text_body": text_body},
    )

    return {"lead_token": create_lead_token(lead.id), "status": lead.status}


@router.patch("/profile", response_model=LeadTokenOut)
async def update_profile(payload: LeadProfileUpdate, lead_id: CurrentLead, db: DB) -> dict:
    lead = await _get_lead(lead_id, db)
    if lead.status == "signed_up":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify your email first.")

    lead = crud.apply_updates(lead, payload.model_dump(exclude_unset=True))
    if lead.status == "otp_verified":
        lead.status = "profile_complete"
    lead = await crud.save(db, lead)
    return {"lead_token": create_lead_token(lead.id), "status": lead.status}


@router.post("/demo-access", response_model=LeadDemoAccessOut)
async def demo_access(
    lead_id: CurrentLead, db: DB, _rl: None = Depends(rate_limit("lead-demo-access", limit=20, window_seconds=3600))
) -> dict:
    """Mints a real token pair for the shared plan="demo" Aurora account —
    no password, since a lead has none for a real account. Same demo
    tenant merchants have always been handed manually; block_demo_writes
    (app/security.py) already makes it read-only regardless of who's
    holding the token."""
    lead = await _get_lead(lead_id, db)
    if lead.status == "signed_up":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify your email first.")

    demo_user = (
        await db.execute(select(User).where(User.email == settings.demo_user_email))
    ).scalar_one_or_none()
    if demo_user is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Demo isn't available right now — please contact us instead.",
        )

    lead.status = "demo_accessed"
    lead.demo_accessed_at = datetime.now(UTC)
    await crud.save(db, lead)

    from app.api.auth import _tokens  # local import: avoids a circular import at module load

    tokens = _tokens(demo_user)
    return tokens.model_dump()


@router.post("/purchase-request", response_model=LeadPurchaseRequestOut)
async def purchase_request(payload: LeadPurchaseRequestIn, lead_id: CurrentLead, db: DB) -> dict:
    lead = await _get_lead(lead_id, db)
    if lead.status == "signed_up":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify your email first.")

    lead.status = "purchase_requested"
    lead.purchase_requested_at = datetime.now(UTC)
    await crud.save(db, lead)

    subject, html_body, text_body = mailer.purchase_request_email(
        lead.email, lead.full_name, lead.phone, lead.shop_name, lead.shop_category, payload.message
    )
    sent = await mailer.send_email(settings.smtp_from_email, subject, html_body, text_body)

    whatsapp_url = None
    if settings.whatsapp_business_number:
        import urllib.parse

        text = (
            f"Hi, I'm interested in Softune. I signed up as {lead.full_name or lead.email}"
            + (f" ({lead.shop_name})" if lead.shop_name else "") + "."
        )
        whatsapp_url = (
            f"https://wa.me/{settings.whatsapp_business_number}?text={urllib.parse.quote(text)}"
        )

    return {"sent": sent, "whatsapp_url": whatsapp_url}
