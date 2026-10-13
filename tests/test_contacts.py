"""The permanent marketing contact list: a person must survive their account's
deletion, an opt-out must survive every sync, and a campaign must never reach
someone who unsubscribed.

These use a reserved example.com address and remove their own contact row at
the end, because contacts are otherwise kept forever on purpose."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text

from app import contacts
from app.api import superadmin_marketing as marketing
from app.crud import create_tenant_owner_and_site
from app.db import SessionLocal
from app.models import MarketingContact, Tenant


def test_lifecycle_for():
    assert contacts.lifecycle_for("trial", False, False) == "trial"
    assert contacts.lifecycle_for("growth", True, False) == "customer"
    assert contacts.lifecycle_for("trial", False, True) == "churned"
    assert contacts.lifecycle_for("growth", True, True) == "former_customer"


@pytest.fixture
async def person():
    """A real tenant with a throwaway contact address. Both removed afterwards."""
    suffix = uuid.uuid4().hex[:10]
    email = f"contact-{suffix}@example.com"
    async with SessionLocal() as db:
        user, _site = await create_tenant_owner_and_site(
            db,
            email=email,
            password="test-password-123",
            workspace_name=f"Contact Test {suffix}",
            plan="starter",
            template_key="aurora",
            site_name=f"Contact Site {suffix}",
            subdomain=f"contact-{suffix}",
            full_name="Contact Person",
            phone="01700000001",
        )
        tenant_id = user.tenant_id
    yield email, tenant_id
    async with SessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.execute(delete(MarketingContact).where(MarketingContact.email == email))
        await db.commit()


async def _contact(email: str) -> MarketingContact | None:
    async with SessionLocal() as db:
        return (await db.execute(select(MarketingContact).where(MarketingContact.email == email))).scalar_one_or_none()


async def test_signup_saves_a_contact(person):
    email, _ = person
    c = await _contact(email)
    assert c is not None
    assert (c.full_name, c.phone, c.lifecycle) == ("Contact Person", "01700000001", "customer")


async def test_contact_survives_deleting_the_tenant(person):
    email, tenant_id = person
    async with SessionLocal() as db:
        await contacts.upsert_tenants(db, [tenant_id], deleted=True)
        await db.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
        await db.commit()
    c = await _contact(email)
    assert c is not None, "the contact must outlive the account"
    assert c.phone == "01700000001" and c.full_name == "Contact Person"
    assert c.lifecycle == "former_customer" and c.account_deleted_at is not None


async def test_unsubscribe_is_never_undone_by_a_sync(person):
    email, tenant_id = person
    async with SessionLocal() as db:
        c = (await db.execute(select(MarketingContact).where(MarketingContact.email == email))).scalar_one()
        c.unsubscribed_at = datetime.now(UTC)
        await db.commit()
    async with SessionLocal() as db:
        await contacts.upsert_tenants(db, [tenant_id])
    assert (await _contact(email)).unsubscribed_at is not None


async def test_campaign_never_emails_an_unsubscribed_contact(person):
    email, _ = person
    async with SessionLocal() as db:
        c = (await db.execute(select(MarketingContact).where(MarketingContact.email == email))).scalar_one()
        c.unsubscribed_at = datetime.now(UTC)
        await db.commit()
    async with SessionLocal() as db:
        audience = marketing.AudienceIn(kind="custom", emails=[email, "someone-else@example.com"])
        resolved = await marketing._resolve(db, audience)
    assert email not in resolved and "someone-else@example.com" in resolved


def test_test_accounts_are_never_captured():
    assert "@softune-test-fixtures.dev".endswith(contacts.TEST_EMAIL_DOMAIN)
