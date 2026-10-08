"""Superadmin billing: the manual payment inbox and storefront logos.

Approving a claim applies a real purchase and emails the owner, so these tests
stick to the paths with no outside effects: rejecting, and refusing to act on
a claim that is no longer pending (the guard that stops an approve and an SMS
match from both applying the same payment)."""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import superadmin_billing as billing
from app.api.superadmin import site_logo_url
from app.db import SessionLocal
from app.models import PaymentClaim, User
from app.security import Principal


def test_site_logo_url_only_for_uploaded_image_logos():
    assert site_logo_url({"logoType": "image", "logoImage": "https://cdn.x/logo.png"}) == "https://cdn.x/logo.png"
    assert site_logo_url({"logoType": "text", "logoImage": "https://cdn.x/logo.png"}) is None
    assert site_logo_url({"logoType": "image", "logoImage": "blob:abc"}) is None
    assert site_logo_url(None) is None


async def _claim(tenant_id: str, status: str = "pending") -> uuid.UUID:
    async with SessionLocal() as db:
        claim = PaymentClaim(
            tenant_id=uuid.UUID(tenant_id),
            kind="image_credits",
            pack_id="test-pack",
            credits=10,
            amount_cents=50_000,
            sender_number="01700000000",
            trx_id=f"TEST{uuid.uuid4().hex[:8].upper()}",
            status=status,
        )
        db.add(claim)
        await db.commit()
        return claim.id


async def _admin(email: str) -> Principal:
    """The same object the real request dependency hands to a superadmin route."""
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    return Principal(user_id=user.id, tenant_id=user.tenant_id, role=user.role, is_superadmin=True)


async def test_reject_marks_claim_and_keeps_reason(account):
    claim_id = await _claim(account.tenant_id)
    admin = await _admin(account.email)
    async with SessionLocal() as db:
        out = await billing.reject_claim(claim_id, billing.RejectIn(reason="No money received"), admin, db)
    assert out["status"] == "rejected"
    assert "No money received" in out["matched_sms"]
    assert account.email in out["matched_sms"]


async def test_cannot_approve_or_reject_a_claim_that_is_not_pending(account):
    claim_id = await _claim(account.tenant_id, status="rejected")
    admin = await _admin(account.email)
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await billing.approve_claim(claim_id, admin, db)
    assert exc.value.status_code == 409
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await billing.reject_claim(claim_id, billing.RejectIn(reason="again"), admin, db)
    assert exc.value.status_code == 409
    async with SessionLocal() as db:
        status = (await db.execute(select(PaymentClaim.status).where(PaymentClaim.id == claim_id))).scalar_one()
    assert status == "rejected"
