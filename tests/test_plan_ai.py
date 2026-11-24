"""Free-plan gates on the AI, the agent, undo and the ChatGPT connection.

Gemini, Cloudinary, the queue and the cache are faked: the test database is the
production one, so nothing here may reach an outside service. Every write below
is to a tenant the test created itself.
"""

import base64
import io
import json
import uuid

import pytest
from PIL import Image
from sqlalchemy import select, update

from app import ai, ai_agent, ai_agent_tools, ai_agent_undo, ai_images, ai_plan, ai_tools, media, mcp_server, oauth_server
from app.api import ai_images as images_api
from app.config import settings
from app.db import SessionLocal
from app.models import AiAgentChange, OAuthToken, Site, Tenant
from tests.test_mcp_oauth import REDIRECT, RESOURCE, _tokens, rpc, tool


@pytest.fixture(autouse=True)
def _no_outside_world(monkeypatch):
    async def noop(*a, **kw):
        return None

    from app import cache, queue

    monkeypatch.setattr(queue, "publish", noop)
    monkeypatch.setattr(cache, "invalidate_site", noop)
    monkeypatch.setattr(cache, "invalidate_dashboard", noop)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")


def _locked(response, feature: str = "ai") -> None:
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "plan_locked" and detail["feature"] == feature
    assert detail["needed_plan"] == "starter" and detail["message"]


async def _set_plan(account, plan: str) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(account.tenant_id)).values(plan=plan))
        await db.commit()


async def _tenant_values(account, **values) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(account.tenant_id)).values(**values))
        await db.commit()


async def _finish_onboarding(account) -> None:
    from datetime import UTC, datetime

    async with SessionLocal() as db:
        await db.execute(
            update(Site).where(Site.id == uuid.UUID(account.site_id)).values(onboarding_completed_at=datetime.now(UTC))
        )
        await db.commit()


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# --- no database ------------------------------------------------------------------

def test_the_feature_map_covers_agent_tools_and_undo_shapes():
    assert ai_plan.tool_feature("update_seo") == "seo"
    assert ai_plan.tool_feature("update_fraud_rules") == "fraud"
    assert ai_plan.tool_feature("update_blocklist") == "fraud"
    assert ai_plan.tool_feature("request_custom_domain") == "domain"
    assert ai_plan.tool_feature("search_customers") == "customers"
    assert ai_plan.tool_feature("get_store_admin_info", {"section": "domain"}) == "domain"
    assert ai_plan.tool_feature("get_store_admin_info", {"section": "fraud"}) == "fraud"
    assert ai_plan.tool_feature("get_store_admin_info", {"section": "invoices"}) is None
    assert ai_plan.tool_feature("update_homepage_text") is None
    assert ai_plan.undo_feature({"type": "site_json", "column": "seo"}) == "seo"
    assert ai_plan.undo_feature({"type": "site_json", "column": "fraud_rules"}) == "fraud"
    assert ai_plan.undo_feature({"type": "site_json", "column": "business"}) is None
    assert ai_plan.undo_feature({"type": "domain_request"}) == "domain"
    assert ai_plan.undo_feature({"type": "blocklist"}) == "fraud"
    # Every mapped name is a real tool, so a rename can't silently unlock one.
    known = {d["name"] for d in ai_agent.TOOLS[0]["functionDeclarations"]}
    assert set(ai_plan.TOOL_FEATURE) <= known


def test_a_dict_detail_becomes_its_message_never_a_python_dict():
    detail = {"code": "plan_locked", "feature": "seo", "needed_plan": "starter", "message": "Needs a bigger plan."}
    assert ai_plan.detail_message(detail) == "Needs a bigger plan."
    assert ai_plan.detail_message("plain") == "plain"
    # ChatGPT's version of the same lock names no plan and no price.
    out = mcp_server._error_result(detail)["error"]
    assert "plan" not in out.lower() and "{" not in out and "upgrade" not in out.lower()
    assert "{" not in mcp_server._error_result({"code": "x", "message": "Nope."})["error"]


# --- sign-up keeps working on Free, everything else is locked --------------------

async def test_free_signup_door_then_everything_locked(account_factory, monkeypatch):
    account = await account_factory("free")
    seen: dict = {"plans": [], "art": 0}

    async def fake_chat(message, history, tenant_id, db, plan):
        seen["plans"].append(plan)
        return "Try a short, warm description.", [], None

    async def fake_image(prompt, tier, reference_images, aspect_ratio):
        return b"fake-image-bytes", "image/png"

    async def fake_art(*a, **kw):
        seen["art"] += 1
        return "a directed brief"

    saved: dict = {}

    def fake_upload(data, *, subdomain, category):
        saved["subdomain"] = subdomain
        return {"url": "https://example.test/x.png"}

    monkeypatch.setattr(ai, "chat_reply", fake_chat)
    monkeypatch.setattr(ai_images, "generate_image", fake_image)
    monkeypatch.setattr(images_api, "art_direct", fake_art)
    monkeypatch.setattr(images_api, "storage_used_for_plan", lambda plan, sub: 0)
    monkeypatch.setattr(media, "upload_image", fake_upload)

    free = {"preset_id": "hero-studio-gradient", "subject": "bags", "aspect_ratio": "16:9", "tier": "standard"}
    freeform = {"prompt": "a calm banner for my shop", "tier": "standard", "include_text": False}

    # Onboarding open: the sign-up doors work.
    assert (await account.get("/ai/images/presets")).status_code == 200
    balance = (await account.get("/ai/images/balance")).json()
    assert balance["onboarding_free_remaining"] > 0
    start = balance["onboarding_free_remaining"]
    chat = await account.post("/ai/chat", json={"message": "write a description for a bag"})
    assert chat.status_code == 200, chat.text
    # The chat door is bounded by the trial's daily counter, not unlimited.
    assert seen["plans"] == [ai_plan.ONBOARDING_CHAT_PLAN]
    assert (await account.get("/ai/usage")).status_code == 200
    # Without asking for the free images, Free gets none: no use_onboarding_free, no door.
    _locked(await account.post("/ai/images/generate", json=free))
    ok = await account.post("/ai/images/generate", json={**free, "use_onboarding_free": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["credits_charged"] == 0 and ok.json()["onboarding_free_remaining"] == start - 1
    # A freeform prompt is art directed, after the free image was claimed.
    ok = await account.post("/ai/images/generate", json={**freeform, "use_onboarding_free": True})
    assert ok.status_code == 200, ok.text
    assert seen["art"] == 1
    saved_ok = await account.post(
        "/ai/images/save-to-gallery", json={"image": {"data_base64": _png_b64(), "mime_type": "image/png"}}
    )
    assert saved_ok.status_code == 200, saved_ok.text

    # Edit is never part of sign-up, even with credits bought earlier.
    await _tenant_values(account, ai_image_credits=50)
    edit = {"prompt": "brighter", "source_image": {"data_base64": _png_b64(), "mime_type": "image/png"}}
    _locked(await account.post("/ai/images/edit", json=edit))
    _locked(await account.post("/ai/images/generate", json={**free, "tier": "premium"}))
    assert (await account.get("/ai/images/balance")).json()["balance"] == 50

    # Free images used up: the door closes.
    await _tenant_values(account, onboarding_free_images_remaining=0)
    _locked(await account.post("/ai/images/generate", json={**free, "use_onboarding_free": True}))

    # Onboarding finished: chat closes too, and so does every other AI route.
    await _tenant_values(account, onboarding_free_images_remaining=3)
    await _finish_onboarding(account)
    _locked(await account.post("/ai/chat", json={"message": "hello"}))
    _locked(await account.post("/ai/images/generate", json={**free, "use_onboarding_free": True}))
    assert len(seen["plans"]) == 1, "a locked chat must not reach the model"

    pack = {"pack_id": "starter", "sender_number": "01700000000", "trx_id": "TRX12345"}
    _locked(await account.post(f"/sites/{account.site_id}/ai/suggest", json={"prompt": "dark and gold"}))
    _locked(await account.post("/ai/generate-text", json={"kind": "product_description", "context": {"name": "Bag"}}))
    _locked(await account.post("/ai/agent/chat", json={"message": "hi"}))
    _locked(await account.post("/ai/chat-credits/purchase", json=pack))
    _locked(await account.post("/ai/images/purchase-credits", json=pack))
    for path, body in (
        ("set-categories", {"categories": ["A"]}),
        ("add-category", {"name": "A"}),
        ("create-product", {"product": {"name": "A"}}),
        ("update-product", {"product": {"product_name": "A"}}),
        ("update-order-status", {"order_number": "1", "status": "paid"}),
        ("create-event", {"name": "Sale", "discount_percent": 10}),
        ("create-ticket", {"subject": "s", "category": "c", "message": "m"}),
    ):
        _locked(await account.post(f"/ai/actions/{path}", json=body))

    # Still open: balance, presets, usage, and saving an image the merchant owns.
    assert (await account.get("/ai/images/balance")).status_code == 200
    assert (await account.get("/ai/images/presets")).status_code == 200
    assert (await account.get("/ai/chat-credits/balance")).status_code == 200
    # No chat credit or image credit was spent by any refused call.
    assert (await account.get("/ai/images/balance")).json()["balance"] == 50


async def test_art_direct_never_runs_before_the_credit_check(account, monkeypatch):
    calls = {"art": 0}

    async def fake_art(*a, **kw):
        calls["art"] += 1
        return "brief"

    async def fake_image(prompt, tier, reference_images, aspect_ratio):
        return b"img", "image/png"

    monkeypatch.setattr(images_api, "art_direct", fake_art)
    monkeypatch.setattr(ai_images, "generate_image", fake_image)
    await _tenant_values(account, ai_image_credits=0, onboarding_free_images_remaining=0)
    body = {"prompt": "a nice banner for my shop", "tier": "standard"}

    refused = await account.post("/ai/images/generate", json=body)
    assert refused.status_code == 402 and calls["art"] == 0

    await _tenant_values(account, ai_image_credits=10)
    ok = await account.post("/ai/images/generate", json=body)
    assert ok.status_code == 200, ok.text
    assert calls["art"] == 1 and ok.json()["credits_charged"] > 0


# --- agent tools and undo after a downgrade ---------------------------------------

async def _tool(account, name: str, /, **args) -> dict:
    async with SessionLocal() as db:
        return await ai_agent_tools.run(name, args, db, uuid.UUID(account.tenant_id), ai_agent._AGENT_HANDLERS)


async def test_a_downgrade_stops_agent_tools_and_undo_of_locked_columns(account_factory, monkeypatch):
    account = await account_factory("starter")
    seo = await _tool(account, "update_seo", title_suffix="Before the downgrade")
    fraud = await _tool(account, "update_fraud_rules", rules=[{"rule": "block_blocklist", "enabled": True}])
    theme = await _tool(account, "update_storefront_design", primary_color="#112233")
    for out in (seo, fraud, theme):
        assert out.get("ok"), out

    await _set_plan(account, "free")

    # Every tool is refused with the plan's own sentence, as text, never a dict.
    for name, args in (
        ("update_seo", {"title_suffix": "After"}),
        ("update_fraud_rules", {"rules": [{"rule": "block_blocklist", "enabled": False}]}),
        ("update_blocklist", {"action": "block", "kind": "phone", "value": "01711111111"}),
        ("request_custom_domain", {"domain": "shop.example.com"}),
        ("get_store_admin_info", {"section": "domain"}),
        ("get_store_admin_info", {"section": "fraud"}),
        ("update_storefront_design", {"primary_color": "#445566"}),
    ):
        out = await _tool(account, name, **args)
        assert out.get("code") == "plan_locked", (name, out)
        assert isinstance(out["error"], str) and "{" not in out["error"], (name, out)
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(account.site_id)))).scalar_one()
        assert site.seo.get("title_suffix") == "Before the downgrade"
        assert site.pending_custom_domain is None
        for read in ("search_customers", "get_fraud_status"):
            out = await ai_tools.execute_tool(read, {}, db, uuid.UUID(account.tenant_id))
            assert "error" in out and "{" not in out["error"], (read, out)

    # A change recorded before the downgrade can't be undone now.
    for change in (seo, fraud, theme):
        _locked(await account.post(f"/ai/changes/{change['change']['id']}/undo"))
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(account.site_id)))).scalar_one()
        assert site.seo.get("title_suffix") == "Before the downgrade"
        rows = (await db.execute(select(AiAgentChange.undone_at).where(AiAgentChange.tenant_id == uuid.UUID(account.tenant_id)))).scalars().all()
        assert rows and all(r is None for r in rows)

    # Another tenant's change id is still a 404, never a plan message.
    assert (await account.post(f"/ai/changes/{uuid.uuid4()}/undo")).status_code == 404

    # Billing status quotes the same storage count the upload paths enforce.
    asked: list = []

    def fake_used(plan, subdomain):
        asked.append(plan)
        return 0

    monkeypatch.setattr(media, "storage_used_for_plan", fake_used)
    async with SessionLocal() as db:
        out = await ai_tools.execute_tool("get_billing_status", {}, db, uuid.UUID(account.tenant_id))
    assert out["plan"] == "free" and asked == ["free"]

    # Upgrading brings it all back.
    await _set_plan(account, "starter")
    assert (await account.post(f"/ai/changes/{seo['change']['id']}/undo")).status_code == 200
    assert (await _tool(account, "update_seo", title_suffix="After"))["ok"]


# --- ChatGPT ----------------------------------------------------------------------

async def test_chatgpt_on_free_is_quiet_not_revoked_and_comes_back(account_factory):
    account = await account_factory("starter")
    tokens = await _tokens(account)
    access = tokens["access_token"]
    assert (await tool(account, access, "get_business_overview"))["isError"] is False

    await _set_plan(account, "free")
    for name, args in (
        ("get_business_overview", {}),
        ("update_homepage_text", {"changes": [{"field": "tagline", "value": "nope"}]}),
        ("undo_change", {"change_id": str(uuid.uuid4())}),
    ):
        result = await tool(account, access, name, args)
        assert result["isError"] is True, name
        text = json.dumps(result).lower()
        for word in ("plan", "upgrade", "price", "starter", "৳", "{\"code"):
            assert word not in result["structuredContent"]["error"].lower(), (name, word)
        assert "plan_locked" not in text
    async with SessionLocal() as db:
        theme = (await db.execute(select(Site.theme).where(Site.id == uuid.UUID(account.site_id)))).scalar_one()
        revoked = (await db.execute(
            select(OAuthToken.revoked_at).where(OAuthToken.tenant_id == uuid.UUID(account.tenant_id))
        )).scalars().all()
    assert (theme or {}).get("tagline") != "nope"
    assert revoked and all(r is None for r in revoked), "the token must not be revoked"

    # Discovery, listing and disconnecting stay open.
    assert (await rpc(account, access, "tools/list")).status_code == 200
    connections = await account.get("/oauth/connections")
    assert connections.status_code == 200 and len(connections.json()) == 1

    # Consent is refused with a message the dashboard can show.
    req = oauth_server.make_request_token({
        "client_id": "x", "scope": "store.read", "redirect_uri": REDIRECT, "state": "s",
        "code_challenge": "c" * 43, "resource": RESOURCE,
    })
    _locked(await account.post("/oauth/consent", json={"request": req, "approve": True}), "chatgpt")
    _locked(await account.get("/oauth/consent", params={"request": req}), "chatgpt")

    await _set_plan(account, "starter")
    assert (await tool(account, access, "get_business_overview"))["isError"] is False

    await _set_plan(account, "free")
    gone = await account.delete(f"/oauth/connections/{connections.json()[0]['id']}")
    assert gone.status_code == 204
