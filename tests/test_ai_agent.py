"""AI agent: the write tools apply immediately and Undo puts things back.

The tools are called directly (no Gemini) so these tests are about the store
changes themselves; the streaming loop is covered by
test_agent_stream_applies_change_and_emits_events with a scripted model.
"""

import json
import uuid

from sqlalchemy import select

from app import ai_agent, ai_agent_tools, ai_agent_undo
from app.db import SessionLocal
from app.models import Category, Product, Site


async def _theme(tenant_id) -> dict:
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.tenant_id == uuid.UUID(tenant_id)))).scalars().first()
        return dict(site.theme or {})


async def _tool(tenant_id, name, **args) -> dict:
    async with SessionLocal() as db:
        return await ai_agent_tools.run(name, args, db, uuid.UUID(tenant_id))


async def _undo(tenant_id, change_id) -> dict:
    async with SessionLocal() as db:
        return await ai_agent_undo.undo(db, uuid.UUID(tenant_id), uuid.UUID(change_id))


async def test_design_change_applies_and_undo_restores(account, site):
    before = await _theme(account.tenant_id)
    result = await _tool(account.tenant_id, "update_storefront_design",
                         primary_color="#c9a227", surface_color="#111111", button_style="Pill")
    assert result["ok"] and result["change"]["kind"] == "theme"
    after = await _theme(account.tenant_id)
    assert after["primaryColor"] == "#C9A227" and after["surfaceColor"] == "#111111"

    await _undo(account.tenant_id, result["change"]["id"])
    restored = await _theme(account.tenant_id)
    for key in ("primaryColor", "surfaceColor", "buttonStyle"):
        assert restored.get(key) == before.get(key)


async def test_bad_color_is_rejected_without_writing(account, site):
    before = await _theme(account.tenant_id)
    result = await _tool(account.tenant_id, "update_storefront_design", primary_color="gold")
    assert "error" in result
    assert await _theme(account.tenant_id) == before


async def test_undo_keeps_a_newer_manual_edit(account, site):
    result = await _tool(account.tenant_id, "update_homepage_text",
                         banner_title="Eid Sale is here", banner_button="Shop now")
    change_id = result["change"]["id"]
    # The merchant then edits the button by hand in the theme editor.
    async with SessionLocal() as db:
        row = (await db.execute(select(Site).where(Site.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
        row.theme = {**row.theme, "ctaButton": "Order today"}
        await db.commit()

    out = await _undo(account.tenant_id, change_id)
    theme = await _theme(account.tenant_id)
    assert theme["ctaButton"] == "Order today", "a later manual edit must survive undo"
    assert theme.get("ctaTitle") != "Eid Sale is here"
    assert out["skipped"] == 1


async def test_undo_twice_is_a_conflict(account, site):
    result = await _tool(account.tenant_id, "update_homepage_text", tagline="Handmade in Dhaka")
    await _undo(account.tenant_id, result["change"]["id"])
    response = await account.post(f"/ai/changes/{result['change']['id']}/undo")
    assert response.status_code == 409


async def test_two_bangla_categories_and_undo(account, site):
    first = await _tool(account.tenant_id, "add_category", name="কুর্তি")
    second = await _tool(account.tenant_id, "add_category", name="পায়জামা")
    assert first["ok"] and second["ok"], (first, second)
    assert first["category"]["slug"] != second["category"]["slug"]

    await _undo(account.tenant_id, second["change"]["id"])
    async with SessionLocal() as db:
        names = (await db.execute(
            select(Category.name).where(Category.tenant_id == uuid.UUID(account.tenant_id))
        )).scalars().all()
    assert names == ["কুর্তি"]


async def test_create_and_update_product_with_undo(account, site):
    created = await _tool(account.tenant_id, "create_product", name="Red Kurti", price_taka=1250, stock=7)
    assert created["ok"], created
    pid = created["product"]["id"]
    updated = await _tool(account.tenant_id, "update_product", product_id=pid, price_taka=990, stock=3)
    assert updated["ok"], updated

    async with SessionLocal() as db:
        row = (await db.execute(select(Product).where(Product.id == uuid.UUID(pid)))).scalar_one()
        assert (row.price_cents, row.stock) == (99_000, 3)

    await _undo(account.tenant_id, updated["change"]["id"])
    async with SessionLocal() as db:
        row = (await db.execute(select(Product).where(Product.id == uuid.UUID(pid)))).scalar_one()
        assert (row.price_cents, row.stock) == (125_000, 7)

    await _undo(account.tenant_id, created["change"]["id"])
    async with SessionLocal() as db:
        gone = (await db.execute(select(Product).where(Product.id == uuid.UUID(pid)))).scalar_one_or_none()
    assert gone is None


async def test_agent_stream_applies_change_and_emits_events(account, site, monkeypatch):
    """A scripted model: round 1 calls update_homepage_text, round 2 answers.
    Checks the real endpoint streams status -> change -> text -> done and the
    store actually changed."""
    rounds = iter([
        [{"text": "Let me write that."},
         {"functionCall": {"name": "update_homepage_text", "args": {"banner_title": "Winter Collection"}}}],
        [{"text": "Done — your banner now says Winter Collection."}],
    ])

    async def fake_round(http, model, contents, tracker):
        for part in next(rounds):
            yield part

    monkeypatch.setattr(ai_agent, "_stream_round", fake_round)
    response = await account.post("/ai/agent/chat", json={"message": "change my banner title to Winter Collection"})
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    types = [e["type"] for e in events]
    assert types[0] == "delta" and "status" in types and "change" in types and types[-1] == "done", types
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "Let me write that.\n\nDone" in text
    assert (await _theme(account.tenant_id))["ctaTitle"] == "Winter Collection"


# --- images: the agent may only use THIS store's own uploads -----------------

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import ai_agent_media  # noqa: E402
from app.config import settings  # noqa: E402


def _url(sub: str, cloud: str | None = None, path: str = "other/a1b2.jpg") -> str:
    cloud = cloud or settings.cloudinary_cloud_name or "democloud"
    return f"https://res.cloudinary.com/{cloud}/image/upload/v1700000000/{settings.cloudinary_root_folder}/{sub}/{path}"


def test_own_image_url_accepts_this_stores_upload(monkeypatch):
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "democloud")
    site = SimpleNamespace(subdomain="elara")
    assert ai_agent_media.own_image_url(site, _url("elara")) == _url("elara")


@pytest.mark.parametrize("bad", [
    "https://evil.example.com/x.jpg",                                   # not Cloudinary at all
    "http://res.cloudinary.com/democloud/image/upload/v1/softune/elara/a.jpg",  # not https
    "https://res.cloudinary.com/otherCloud/image/upload/v1/softune/elara/a.jpg",  # someone else's cloud
    "https://res.cloudinary.com/democloud/image/upload/v1/softune/other-store/a.jpg",  # another store's folder
    "https://res.cloudinary.com/democloud/image/upload/v1/softune/elara/../other-store/a.jpg",  # path trick
    "https://res.cloudinary.com/democloud/image/upload/v1/softune/elara/a.jpg?x=https://evil",  # query smuggling
    "",
])
def test_own_image_url_rejects_everything_else(monkeypatch, bad):
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "democloud")
    with pytest.raises(HTTPException) as exc:
        ai_agent_media.own_image_url(SimpleNamespace(subdomain="elara"), bad)
    assert exc.value.status_code == 422


async def test_placing_a_foreign_image_is_refused_and_changes_nothing(account, site):
    before = await _theme(account.tenant_id)
    result = await _tool(account.tenant_id, "set_homepage_image", slot="hero",
                         image_url="https://evil.example.com/x.jpg")
    assert "error" in result
    assert await _theme(account.tenant_id) == before


async def test_image_placement_and_undo(account, site, monkeypatch):
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "democloud")
    sub = site["subdomain"]
    photo = _url(sub)

    hero = await _tool(account.tenant_id, "set_homepage_image", slot="hero", image_url=photo)
    assert hero["ok"], hero
    assert (await _theme(account.tenant_id))["heroImages"] == [photo]
    await _undo(account.tenant_id, hero["change"]["id"])
    assert (await _theme(account.tenant_id)).get("heroImages") != [photo]

    await _tool(account.tenant_id, "add_category", name="Serums")
    cat = await _tool(account.tenant_id, "set_category_image", category_name="serums", image_url=photo)
    assert cat["ok"], cat
    async with SessionLocal() as db:
        row = (await db.execute(select(Category).where(Category.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
        assert row.image_url == photo
    await _undo(account.tenant_id, cat["change"]["id"])
    async with SessionLocal() as db:
        row = (await db.execute(select(Category).where(Category.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
        assert row.image_url is None

    prod = await _tool(account.tenant_id, "create_product", name="Centella Serum", price_taka=1450)
    added = await _tool(account.tenant_id, "add_product_image", product_id=prod["product"]["id"], image_url=photo)
    assert added["ok"], added
    async with SessionLocal() as db:
        row = (await db.execute(select(Product).where(Product.id == uuid.UUID(prod["product"]["id"])))).scalar_one()
        assert [i["url"] for i in row.images] == [photo]
    await _undo(account.tenant_id, added["change"]["id"])
    async with SessionLocal() as db:
        row = (await db.execute(select(Product).where(Product.id == uuid.UUID(prod["product"]["id"])))).scalar_one()
        assert row.images == []
