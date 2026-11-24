"""Product, event and storage limits on every create/upload path (Task 7).

Real database (own throwaway tenants only), fake Cloudinary and fake Gemini:
nothing here may reach an outside service. Nobody is on "free" in production
yet, so these tenants are the only free ones that exist.
"""

import asyncio
import io
import uuid

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import event as sa_event
from sqlalchemy import func, select

from app import ai_actions, ai_agent, ai_agent_tools, mcp_images, media
from app import ai_site_setup as setup
from app.db import SessionLocal, engine
from app.models import Event, Product, Site, Tenant

MB = 1024 * 1024


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------


async def _seed_products(account, n: int) -> None:
    async with SessionLocal() as db:
        for i in range(n):
            db.add(Product(
                tenant_id=uuid.UUID(account.tenant_id), site_id=uuid.UUID(account.site_id),
                name=f"Seed {i}", slug=f"seed-{i}", currency="BDT",
            ))
        await db.commit()


async def _seed_events(account, n: int) -> None:
    async with SessionLocal() as db:
        for i in range(n):
            db.add(Event(
                tenant_id=uuid.UUID(account.tenant_id), site_id=uuid.UUID(account.site_id),
                name=f"Seed {i}", slug=f"seed-ev-{i}", discount_percent=10,
            ))
        await db.commit()


async def _count(model, account) -> int:
    async with SessionLocal() as db:
        return (await db.execute(
            select(func.count()).select_from(model).where(model.tenant_id == uuid.UUID(account.tenant_id))
        )).scalar_one()


def _product_body(name="Over") -> dict:
    return {"name": name, "price_cents": 1000, "currency": "BDT"}


def _event_body(name="Over") -> dict:
    return {"name": name, "discount_percent": 10}


def _png(size=(20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  products: HTTP, AI confirm, agent tool
# ---------------------------------------------------------------------------


async def test_free_product_11_is_403_over_http(account_factory):
    free = await account_factory("free")
    await _seed_products(free, 10)
    res = await free.post(f"/sites/{free.site_id}/products", json=_product_body())
    assert res.status_code == 403
    assert isinstance(res.json()["detail"], str)  # plain string, not plan_locked
    assert await _count(Product, free) == 10


async def test_free_product_10_is_allowed(account_factory):
    free = await account_factory("free")
    await _seed_products(free, 9)
    res = await free.post(f"/sites/{free.site_id}/products", json=_product_body())
    assert res.status_code == 201


async def test_business_is_unlimited_and_never_takes_the_lock(account_factory):
    biz = await account_factory("business")
    await _seed_products(biz, 12)  # past every other plan's cap

    statements: list[str] = []

    def _spy(conn, cursor, statement, *a):
        statements.append(statement)

    sa_event.listen(engine.sync_engine, "before_cursor_execute", _spy)
    try:
        res = await biz.post(f"/sites/{biz.site_id}/products", json=_product_body())
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", _spy)
    assert res.status_code == 201
    assert not any("FOR NO KEY UPDATE" in s or "FOR UPDATE" in s for s in statements)


async def test_limited_plan_does_lock_the_tenant_row(account_factory):
    free = await account_factory("free")
    statements: list[str] = []

    def _spy(conn, cursor, statement, *a):
        statements.append(statement)

    sa_event.listen(engine.sync_engine, "before_cursor_execute", _spy)
    try:
        res = await free.post(f"/sites/{free.site_id}/products", json=_product_body())
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", _spy)
    assert res.status_code == 201
    locks = [s for s in statements if "FOR NO KEY UPDATE" in s]
    assert len(locks) == 1 and "tenants" in locks[0]


async def test_two_concurrent_creates_at_nine_leave_exactly_ten(account_factory):
    free = await account_factory("free")
    await _seed_products(free, 9)
    results = await asyncio.gather(
        free.post(f"/sites/{free.site_id}/products", json=_product_body("A")),
        free.post(f"/sites/{free.site_id}/products", json=_product_body("B")),
    )
    assert sorted(r.status_code for r in results) == [201, 403]
    assert await _count(Product, free) == 10


async def test_ai_confirm_create_product_is_capped_on_free(account_factory):
    free = await account_factory("free")
    await _seed_products(free, 10)
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await ai_actions.create_product(db, uuid.UUID(free.tenant_id), {"name": "Over", "price_cents": 100})
    assert exc.value.status_code == 403
    assert await _count(Product, free) == 10


async def test_agent_and_mcp_create_product_tool_is_capped_on_free(account_factory):
    """The MCP server dispatches to this same handler table (ai_agent_tools.run
    over ai_agent_products.HANDLERS), so one test covers both."""
    free = await account_factory("free")
    await _seed_products(free, 10)
    async with SessionLocal() as db:
        out = await ai_agent_tools.run(
            "create_product", {"name": "Over", "price_taka": 100}, db,
            uuid.UUID(free.tenant_id), ai_agent._AGENT_HANDLERS,
        )
    assert "Product limit reached" in out["error"]
    assert await _count(Product, free) == 10


async def test_agent_create_product_unlimited_on_business(account_factory):
    biz = await account_factory("business")
    await _seed_products(biz, 12)
    async with SessionLocal() as db:
        out = await ai_agent_tools.run(
            "create_product", {"name": "Fine", "price_taka": 100}, db,
            uuid.UUID(biz.tenant_id), ai_agent._AGENT_HANDLERS,
        )
    assert out.get("ok") is True


# ---------------------------------------------------------------------------
#  events
# ---------------------------------------------------------------------------


async def test_free_event_4_is_403_over_http(account_factory):
    free = await account_factory("free")
    await _seed_events(free, 3)
    res = await free.post(f"/sites/{free.site_id}/events", json=_event_body())
    assert res.status_code == 403
    assert isinstance(res.json()["detail"], str)
    assert await _count(Event, free) == 3


async def test_free_event_3_is_allowed(account_factory):
    free = await account_factory("free")
    await _seed_events(free, 2)
    res = await free.post(f"/sites/{free.site_id}/events", json=_event_body())
    assert res.status_code == 201


async def test_two_concurrent_event_creates_at_two_leave_exactly_three(account_factory):
    free = await account_factory("free")
    await _seed_events(free, 2)
    results = await asyncio.gather(
        free.post(f"/sites/{free.site_id}/events", json=_event_body("A")),
        free.post(f"/sites/{free.site_id}/events", json=_event_body("B")),
    )
    assert sorted(r.status_code for r in results) == [201, 403]
    assert await _count(Event, free) == 3


async def test_ai_confirm_create_event_is_capped_on_free(account_factory):
    free = await account_factory("free")
    await _seed_events(free, 3)
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await ai_actions.create_event(db, uuid.UUID(free.tenant_id), name="Over", discount_percent=10)
    assert exc.value.status_code == 403
    assert await _count(Event, free) == 3


# ---------------------------------------------------------------------------
#  storage: site_storage_used_bytes
# ---------------------------------------------------------------------------


class _FakeAdmin:
    """Stands in for cloudinary.api.resources. `pages` maps (resource_type,
    cursor) -> response."""

    def __init__(self, pages):
        self.pages = pages
        self.calls: list[dict] = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.pages[(kw.get("resource_type", "image"), kw.get("next_cursor"))]


def _res(public_id, nbytes):
    return {"secure_url": f"https://x/{public_id}", "public_id": public_id, "bytes": nbytes}


@pytest.fixture
def fake_admin(monkeypatch):
    monkeypatch.setattr(media, "_ensure_configured", lambda: None)

    def install(pages):
        fake = _FakeAdmin(pages)
        monkeypatch.setattr(media.cloudinary.api, "resources", fake)
        return fake

    return install


def test_default_storage_count_is_todays_behaviour(fake_admin):
    fake = fake_admin({("image", None): {"resources": [_res("a", 5)]}})
    assert media.site_storage_used_bytes("shop") == 5 * len(media.VALID_CATEGORIES)
    # One image listing per category, no video listing, no cursor.
    assert len(fake.calls) == len(media.VALID_CATEGORIES)
    assert all("resource_type" not in c and "next_cursor" not in c for c in fake.calls)


def test_full_storage_count_includes_videos_and_follows_cursors(fake_admin):
    root = media.settings.cloudinary_root_folder.strip("/")
    fake = fake_admin({
        ("image", None): {"resources": [_res(f"{root}/shop/hero/a", 10)], "next_cursor": "c1"},
        ("image", "c1"): {"resources": [_res(f"{root}/shop/hero/b", 20)]},
        ("video", None): {"resources": [_res(f"{root}/shop/hero/v", 1000)]},
    })
    assert media.site_storage_used_bytes("shop", full=True) == 1030
    assert [c.get("next_cursor") for c in fake.calls] == [None, "c1", None]


def test_full_storage_count_skips_system_screenshots(fake_admin):
    root = media.settings.cloudinary_root_folder.strip("/")
    fake_admin({
        ("image", None): {"resources": [_res(f"{root}/shop/_system/s", 999), _res(f"{root}/shop/hero/a", 7)]},
        ("video", None): {"resources": []},
    })
    assert media.site_storage_used_bytes("shop", full=True) == 7


def test_full_storage_count_caps_pages(fake_admin):
    root = media.settings.cloudinary_root_folder.strip("/")
    endless = {("image", None): {"resources": [_res(f"{root}/shop/hero/a", 1)], "next_cursor": "n"},
               ("image", "n"): {"resources": [_res(f"{root}/shop/hero/a", 1)], "next_cursor": "n"},
               ("video", None): {"resources": []}}
    fake = fake_admin(endless)
    media.site_storage_used_bytes("shop", full=True)
    assert len(fake.calls) <= media.FULL_SCAN_MAX_PAGES + 1


def test_only_free_plan_uses_the_full_count(monkeypatch):
    seen = []
    monkeypatch.setattr(media, "site_storage_used_bytes", lambda sub, *, full=False: seen.append(full) or 0)
    for plan in ("free", "starter", "growth", "business", "trial", "demo"):
        media.storage_used_for_plan(plan, "shop")
    assert seen == [True, False, False, False, False, False]


# ---------------------------------------------------------------------------
#  storage: every upload path refuses past the limit on free, BEFORE uploading
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_full(monkeypatch):
    """Cloudinary pretends the site holds 100MB minus 10 bytes, and any upload
    is a test failure. Records the `full` flag each path asked for."""
    seen: list[bool] = []

    def used(subdomain, *, full=False):
        seen.append(full)
        return 100 * MB - 10

    def boom(*a, **k):
        raise AssertionError("an upload reached Cloudinary")

    monkeypatch.setattr(media, "site_storage_used_bytes", used)
    monkeypatch.setattr(media, "upload_image", boom)
    monkeypatch.setattr(media, "upload_video", boom)
    return seen


async def test_media_image_upload_refused_on_free(account_factory, storage_full):
    free = await account_factory("free")
    res = await free.post(
        f"/sites/{free.site_id}/media", params={"category": "other"},
        files={"file": ("a.png", _png(), "image/png")},
    )
    assert res.status_code == 413 and "100MB" in res.json()["detail"]
    assert storage_full == [True]


async def test_media_video_upload_refused_on_free(account_factory, storage_full):
    free = await account_factory("free")
    res = await free.post(
        f"/sites/{free.site_id}/media/video", params={"category": "other"},
        files={"file": ("a.mp4", b"x" * 64, "video/mp4")},
    )
    assert res.status_code == 413
    assert storage_full == [True]


async def test_media_upload_on_paid_plan_keeps_the_cheap_count(account_factory, storage_full, monkeypatch):
    starter = await account_factory("starter")
    monkeypatch.setattr(media, "upload_image", lambda *a, **k: {"url": "https://x/y"})
    res = await starter.post(
        f"/sites/{starter.site_id}/media", params={"category": "other"},
        files={"file": ("a.png", _png(), "image/png")},
    )
    assert res.status_code == 201
    assert storage_full == [False]


async def test_save_to_gallery_refused_on_free(account_factory, storage_full):
    import base64
    free = await account_factory("free")
    res = await free.post("/ai/images/save-to-gallery", json={
        "image": {"data_base64": base64.b64encode(_png()).decode(), "mime_type": "image/png"},
        "category": "other",
    })
    assert res.status_code == 413, res.text
    assert storage_full == [True]


async def test_mcp_add_image_refused_on_free(account_factory, storage_full, monkeypatch):
    free = await account_factory("free")

    async def fake_download(url):
        return _png()

    monkeypatch.setattr(mcp_images, "_download", fake_download)
    async with SessionLocal() as db:
        out = await mcp_images.add_image_to_gallery(
            db, uuid.UUID(free.tenant_id), {"download_url": "https://files.oaiusercontent.com/x"}
        )
    assert "storage is full" in out["error"]
    assert storage_full == [True]


async def test_ai_setup_image_refused_on_free_when_full(account_factory, storage_full):
    free = await account_factory("free")
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(free.site_id)))).scalar_one()
        tenant = (await db.execute(select(Tenant).where(Tenant.id == uuid.UUID(free.tenant_id)))).scalar_one()
        with pytest.raises(HTTPException) as exc:
            await setup.store_image(site, tenant, b"x" * 64, "hero")
    assert exc.value.status_code == 413
    assert storage_full == [True]


async def test_ai_setup_image_works_for_a_new_free_store(account_factory, monkeypatch):
    """Five onboarding images on a store far below 100MB must still go through."""
    free = await account_factory("free")
    monkeypatch.setattr(media, "site_storage_used_bytes", lambda sub, *, full=False: 3 * MB)
    monkeypatch.setattr(media, "upload_image", lambda b, *, subdomain, category: {"url": f"https://x/{category}"})
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(free.site_id)))).scalar_one()
        tenant = (await db.execute(select(Tenant).where(Tenant.id == uuid.UUID(free.tenant_id)))).scalar_one()
        urls = [await setup.store_image(site, tenant, b"x" * (MB // 2), "hero") for _ in range(5)]
    assert len(urls) == 5
