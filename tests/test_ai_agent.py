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


# Positional-only (the "/"): tools take an argument called `name` (add_category,
# create_product), which would otherwise collide with this parameter. Uses the
# agent's full handler table, as the real agent loop does, so image and
# settings tools resolve too.
async def _tool(tenant_id, tool, /, **args) -> dict:
    async with SessionLocal() as db:
        return await ai_agent_tools.run(tool, args, db, uuid.UUID(tenant_id), ai_agent._AGENT_HANDLERS)


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


# --- settings tools ----------------------------------------------------------

async def _site_row(tenant_id) -> Site:
    async with SessionLocal() as db:
        return (await db.execute(select(Site).where(Site.tenant_id == uuid.UUID(tenant_id)))).scalars().first()


_settings = _tool


async def test_delivery_locations_reach_products_and_undo_restores(account, site):
    made = await _tool(account.tenant_id, "create_product", name="Honey", price_taka=500)
    result = await _settings(account.tenant_id, "set_delivery_locations", locations=[
        {"name": "Inside Dhaka", "charge_taka": 60}, {"name": "Outside Dhaka", "charge_taka": 120},
    ])
    assert result["ok"]
    async with SessionLocal() as db:
        product = await db.get(Product, uuid.UUID(made["product"]["id"]))
        # Checkout reads the product, so this is what proves customers get charged.
        assert product.free_delivery is False
        assert {"name": "Inside Dhaka", "charge_cents": 6000} in product.delivery_charges

    later = await _tool(account.tenant_id, "create_product", name="Ghee", price_taka=900)
    async with SessionLocal() as db:
        assert (await db.get(Product, uuid.UUID(later["product"]["id"]))).free_delivery is False

    await _undo(account.tenant_id, result["change"]["id"])
    assert not ((await _site_row(account.tenant_id)).shipping or {}).get("locations")
    async with SessionLocal() as db:
        product = await db.get(Product, uuid.UUID(made["product"]["id"]))
        assert product.free_delivery is True and product.delivery_charges == []


async def test_tracking_id_that_could_inject_script_is_refused(account, site):
    result = await _settings(account.tenant_id, "update_seo", facebook_pixel="1');alert(1);//")
    assert "error" in result
    assert not ((await _site_row(account.tenant_id)).seo or {}).get("facebook_pixel")


async def test_seo_undo_keeps_a_newer_manual_edit(account, site):
    result = await _settings(account.tenant_id, "update_seo", meta_description="Fresh honey", keywords="honey")
    async with SessionLocal() as db:
        row = (await db.execute(select(Site).where(Site.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
        row.seo = {**row.seo, "keywords": "hand edited"}
        await db.commit()
    await _undo(account.tenant_id, result["change"]["id"])
    seo = (await _site_row(account.tenant_id)).seo
    assert seo["keywords"] == "hand edited" and not seo.get("meta_description")


async def test_contact_faqs_and_legal_write_the_page_shapes(account, site):
    assert (await _settings(account.tenant_id, "update_contact_info", phone="+8801711000000", city="Dhaka"))["ok"]
    faqs = await _settings(account.tenant_id, "set_faqs", faqs=[{"question": "COD?", "answer": "Yes."}])
    assert (await _settings(account.tenant_id, "update_legal_page", page="terms", content="Terms text."))["ok"]
    row = await _site_row(account.tenant_id)
    assert row.business["phone"] == "+8801711000000" and row.business["address"]["city"] == "Dhaka"
    assert row.faqs[0]["id"] and row.faqs[0]["answer"] == "Yes."
    assert row.legal["terms"] == {"title": "Terms", "content": "Terms text.", "published": False}
    await _undo(account.tenant_id, faqs["change"]["id"])
    assert (await _site_row(account.tenant_id)).faqs == []


async def test_empty_legal_page_cannot_be_published(account, site):
    assert "error" in await _settings(account.tenant_id, "update_legal_page", page="privacy", published=True)


async def test_domain_request_stays_pending_and_undo_cancels(account, site):
    result = await _settings(account.tenant_id, "request_custom_domain", domain="https://Shop.Example-Store.com/")
    row = await _site_row(account.tenant_id)
    assert row.pending_custom_domain == "shop.example-store.com" and row.custom_domain is None
    await _undo(account.tenant_id, result["change"]["id"])
    assert (await _site_row(account.tenant_id)).pending_custom_domain is None


async def test_block_phone_and_undo_unblocks(account, site):
    result = await _settings(account.tenant_id, "update_blocklist", action="block", kind="phone", value="01711000000")
    assert result["ok"]
    info = await _settings(account.tenant_id, "get_store_admin_info", section="fraud")
    assert [p["phone"] for p in info["blocked_phones"]] == ["01711000000"]
    await _undo(account.tenant_id, result["change"]["id"])
    assert (await _settings(account.tenant_id, "get_store_admin_info", section="fraud"))["blocked_phones"] == []


def test_every_new_tool_has_a_handler_and_status_label():
    from app import ai_agent_admin, ai_agent_settings
    for decl in ai_agent_settings.DECLARATIONS + ai_agent_admin.DECLARATIONS:
        name = decl["name"]
        assert name in ai_agent._LABELS, name
        assert name in ai_agent._AGENT_HANDLERS or name in ai_agent_admin.PROPOSAL_TOOLS, name


# --- full product tools -------------------------------------------------------

def test_description_html_keeps_formatting_and_drops_script():
    from app.ai_agent_products import clean_html

    out = clean_html('<p onclick="x()">Soft <b>silk</b></p><script>alert(1)</script><a href="javascript:evil()">bad</a><a href="https://ok.example/p">good</a><img src=x onerror=y>')
    assert out == '<p>Soft <b>silk</b></p>bad<a href="https://ok.example/p" target="_blank" rel="noopener noreferrer">good</a>'
    assert clean_html("Line one\nline two\n\nPrice < 5 & more") == "<p>Line one<br>line two</p><p>Price &lt; 5 &amp; more</p>"


def test_adding_colors_keeps_existing_values_and_only_adds_new_ones():
    from app.ai_agent_products import _merge_values

    current = [{"type": "Color", "isColor": True, "affectsPrice": False,
                "values": [{"value": "Blush", "hex": "#F4C2C2"}, {"value": "Ivory", "hex": "#FFFFF0"}]}]
    merged = _merge_values(current, [{"type": "color", "values": [{"value": "blush"}, {"value": "Lavender", "hex": "#B57EDC"}]}])
    assert [v["value"] for v in merged[0]["values"]] == ["Blush", "Ivory", "Lavender"]
    assert merged[0]["values"][2]["hex"] == "#B57EDC"
    assert len(current[0]["values"]) == 2  # the stored product is not edited in place


def test_combinations_keep_saved_stock_and_start_new_ones_at_the_given_stock():
    from app.ai_agent_products import _combinations

    options = [{"type": "Color", "values": [{"value": "Blush"}, {"value": "Lavender"}]}, {"type": "Size", "values": [{"value": "M"}]}]
    existing = [{"key": "Color:Blush|Size:M", "optionValues": {"Color": "Blush", "Size": "M"}, "stock": 9, "trackStock": True, "isActive": True}]
    rows = _combinations(options, existing, [{"options": [{"type": "Color", "value": "Lavender"}, {"type": "Size", "value": "M"}], "price_taka": 1500}], 4)
    by_key = {r["key"]: r for r in rows}
    assert by_key["Color:Blush|Size:M"]["stock"] == 9
    assert by_key["Color:Lavender|Size:M"]["stock"] == 4 and by_key["Color:Lavender|Size:M"]["priceCents"] == 150_000
    assert _combinations(options, [], [], None) is None  # plain pooled stock stays as it is
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _combinations(options, existing, [{"options": [{"type": "Color", "value": "Green"}]}], None)


async def test_product_with_every_field_and_adding_colors_with_undo(account, site):
    await _tool(account.tenant_id, "add_category", name="Dupatta")
    await _tool(account.tenant_id, "set_delivery_locations", locations=[{"name": "Inside Dhaka", "charge_taka": 60}, {"name": "Outside Dhaka", "charge_taka": 120}])
    made = await _tool(
        account.tenant_id, "create_product", name="Blush Organza Dupatta", price_taka=1800, compare_at_price_taka=2200,
        cost_price_taka=900, stock=20, sku="DUP-001", unit="pcs", category_name="dupatta", serial_number="SN-1",
        video_url="https://example.com/v.mp4", initial_sold_count=12, track_stock=True,
        description="Hand finished.\n\nZari border.", features=[{"title": "Free gift box", "icon": "gift"}],
        delivery_locations=["Inside Dhaka"],
        variants=[{"type": "Color", "is_color": True, "values": [{"value": "Blush", "hex": "#F4C2C2"}, {"value": "Ivory", "hex": "#FFFFF0"}]}],
    )
    assert made["ok"], made
    pid = made["product"]["id"]
    async with SessionLocal() as db:
        row = await db.get(Product, uuid.UUID(pid))
        assert (row.sku, row.unit, row.compare_at_cents, row.cost_price_cents, row.currency) == ("DUP-001", "pcs", 220_000, 90_000, "BDT")
        assert row.category_id is not None and row.initial_sold_count == 12 and row.video_url.startswith("https://")
        assert row.description == "<p>Hand finished.</p><p>Zari border.</p>"
        assert row.free_delivery is False and row.delivery_charges == [{"name": "Inside Dhaka", "charge_cents": 6000}]
        assert [v["value"] for v in row.attributes["variants"][0]["values"]] == ["Blush", "Ivory"]

    added = await _tool(account.tenant_id, "update_product", product_id=pid,
                        add_variant_values=[{"type": "Color", "values": [{"value": "Lavender", "hex": "#B57EDC"}]}])
    assert added["ok"], added
    async with SessionLocal() as db:
        row = await db.get(Product, uuid.UUID(pid))
        assert [v["value"] for v in row.attributes["variants"][0]["values"]] == ["Blush", "Ivory", "Lavender"]

    sized = await _tool(account.tenant_id, "update_product", product_id=pid,
                        add_variant_values=[{"type": "Size", "values": [{"value": "S"}, {"value": "M"}]}], variant_stock=5,
                        combinations=[{"options": [{"type": "Color", "value": "Blush"}, {"value": "S", "type": "Size"}], "price_taka": 1700, "stock": 2}])
    assert sized["ok"] and sized["product"]["combination_count"] == 6, sized
    async with SessionLocal() as db:
        combos = {c["key"]: c for c in (await db.get(Product, uuid.UUID(pid))).attributes["combinations"]}
        assert combos["Color:Blush|Size:S"]["priceCents"] == 170_000 and combos["Color:Blush|Size:S"]["stock"] == 2
        assert combos["Color:Ivory|Size:M"]["stock"] == 5

    await _undo(account.tenant_id, sized["change"]["id"])
    await _undo(account.tenant_id, added["change"]["id"])
    async with SessionLocal() as db:
        row = await db.get(Product, uuid.UUID(pid))
        assert [v["value"] for v in row.attributes["variants"][0]["values"]] == ["Blush", "Ivory"]
        assert "combinations" not in row.attributes


async def test_product_rejects_unknown_category_delivery_and_script_in_description(account, site):
    assert "error" in await _tool(account.tenant_id, "create_product", name="X", price_taka=10, category_name="Nope")
    assert "error" in await _tool(account.tenant_id, "create_product", name="X", price_taka=10, delivery_locations=["Mars"])
    ok = await _tool(account.tenant_id, "create_product", name="Safe", price_taka=10, description="<script>alert(1)</script>Nice")
    async with SessionLocal() as db:
        assert (await db.get(Product, uuid.UUID(ok["product"]["id"]))).description == "Nice"


async def test_free_delivery_over_sets_and_undo_clears(account, site):
    await _tool(account.tenant_id, "set_delivery_locations", locations=[{"name": "Dhaka", "charge_taka": 60}])
    result = await _tool(account.tenant_id, "set_free_delivery_over", amount_taka=2000)
    assert result["ok"], result
    shipping = (await _site_row(account.tenant_id)).shipping
    assert shipping["free_over_cents"] == 200_000 and shipping["locations"]
    await _undo(account.tenant_id, result["change"]["id"])
    assert not (await _site_row(account.tenant_id)).shipping.get("free_over_cents")
