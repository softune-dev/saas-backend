"""Shared test fixtures.

IMPORTANT: these tests run against your REAL Supabase database, not a fake one.

That is a deliberate tradeoff. A mocked database would not exercise the things
most likely to break — the actual constraints, cascades, citext behaviour and
index-backed queries that migrations/*.sql define. Testing against the real
engine is the only way to know those work.

To keep it safe, every test creates its OWN tenant with a random email, and the
fixture deletes that tenant afterwards. Because every table cascades from
tenants, one DELETE removes all traces. Tests never touch each other's data and
never leave residue behind.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.crud import create_tenant_owner_and_site
from app.db import SessionLocal, engine
from app.main import app
from app.models import Tenant, Template
from app.security import create_access_token


@pytest.fixture(autouse=True)
async def _dispose_engine() -> AsyncGenerator[None, None]:
    """Close pooled database connections after every test.

    WHY THIS IS NECESSARY (and why omitting it produces baffling errors):
    pytest-asyncio gives each test its own event loop, but `engine` is created
    once at import time and its connection pool binds sockets to whichever loop
    was running when they opened. A connection pooled during test 1 is reused in
    test 2 under a DIFFERENT loop, and asyncpg raises

        RuntimeError: Task got Future attached to a different loop

    seemingly at random, usually in whichever test happens to run second.

    Disposing after each test forces fresh connections. It costs a reconnect per
    test — noticeable over the network to Supabase, but correctness first.
    """
    yield
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated HTTP client wired straight to the app.

    ASGITransport calls the app in-process — no network, no running uvicorn
    needed. Tests are fast and you can run them while the dev server is stopped.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


class Account:
    """A registered tenant plus a ready-to-use authorised client."""

    def __init__(self, client: AsyncClient, email: str, tenant_id: str, token: str):
        self.client = client
        self.email = email
        self.tenant_id = tenant_id
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    async def get(self, url: str, **kw):
        return await self.client.get(url, headers=self.headers, **kw)

    async def post(self, url: str, **kw):
        return await self.client.post(url, headers=self.headers, **kw)

    async def patch(self, url: str, **kw):
        return await self.client.patch(url, headers=self.headers, **kw)

    async def delete(self, url: str, **kw):
        return await self.client.delete(url, headers=self.headers, **kw)


async def _register(client: AsyncClient) -> Account:
    """Create a brand-new isolated tenant. Random email so parallel runs never clash.

    POST /auth/register no longer exists (public self-signup was closed —
    this is a paid-only service; accounts are created directly via
    crud.create_tenant_owner_and_site, same as scripts/create_account.py
    does after payment is received). Calling that function directly here is
    the fixture's equivalent of "signing up" — it's the same account-
    creation path production uses now, just invoked in-process instead of
    over HTTP, since there's no public endpoint left to hit.
    """
    suffix = uuid.uuid4().hex[:12]
    # .test is an RFC 2606 reserved TLD — email_validator (used by pydantic's
    # EmailStr on RegisterIn) rejects it outright, so a fixture email can't
    # use it even though it reads like the obvious choice for test data.
    email = f"test-{suffix}@softune-test-fixtures.dev"
    async with SessionLocal() as db:
        user, _site = await create_tenant_owner_and_site(
            db,
            email=email,
            password="test-password-123",
            workspace_name=f"Test WS {suffix}",
            plan="demo",
            template_key="aurora",
            site_name=f"Test Site {suffix}",
            subdomain=f"test-fixture-{suffix}",
            full_name="Test User",
        )
    token = create_access_token(user.id, user.tenant_id, user.role)
    return Account(client, email, str(user.tenant_id), token)


async def _cleanup(tenant_ids: list[str]) -> None:
    """One DELETE per tenant removes every row it owns, via ON DELETE CASCADE."""
    async with SessionLocal() as db:
        for tid in tenant_ids:
            await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()


@pytest.fixture
async def account(client: AsyncClient) -> AsyncGenerator[Account, None]:
    """A logged-in tenant. Deleted after the test."""
    acct = await _register(client)
    yield acct
    await _cleanup([acct.tenant_id])


@pytest.fixture
async def two_accounts(
    client: AsyncClient,
) -> AsyncGenerator[tuple[Account, Account], None]:
    """Two unrelated tenants — the fixture the isolation tests are built on."""
    a = await _register(client)
    b = await _register(client)
    yield a, b
    await _cleanup([a.tenant_id, b.tenant_id])


@pytest.fixture
async def template_id() -> str:
    """Any active template, from the seed data.

    Fails with a clear instruction rather than a confusing IndexError if the seed
    migration has not been run yet.
    """
    async with SessionLocal() as db:
        row = (
            await db.execute(select(Template.id).where(Template.is_active).limit(1))
        ).scalar_one_or_none()
    assert row is not None, (
        "No templates found. Run migrations/004_seed.sql in the Supabase SQL Editor."
    )
    return str(row)


@pytest.fixture
async def site(account: Account, template_id: str) -> dict:
    """A site belonging to `account`, ready to hang pages and products off."""
    response = await account.post(
        "/sites",
        json={
            "template_id": template_id,
            "name": "Test Site",
            "subdomain": f"test-{uuid.uuid4().hex[:10]}",
        },
    )
    assert response.status_code == 201, f"site create failed: {response.text}"
    return response.json()
