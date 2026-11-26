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

from fastapi import HTTPException

from app import ai, ai_agent, ai_agent_admin, ai_agent_tools, claims, mailer, notifications, ai_agent_undo, ai_images, ai_plan, ai_tools, media, mcp_server, oauth_server
from app.api import ai_images as images_api
from app.config import settings
from app.db import SessionLocal
from app.models import AiAgentChange, OAuthToken, PaymentClaim, Site, Tenant
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


# --- Free: setup has its own doors, then the ordinary AI with 10 a day -----------

async def test_free_signup_doors_then_the_ordinary_ai(account_factory, monkeypatch):
    account = await account_factory("free")
    seen: dict = {"plans": [], "art": 0}

    async def fake_chat(message, history, tenant_id, db, plan, text_only=False):
        seen["plans"].append((plan, text_only))
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
    assert seen["plans"] == [("free", True)]
    assert (await account.get("/ai/usage")).status_code == 200
    # During setup a standard image uses a free one, as on every plan.
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

    # Edit and the premium tier cost credits, as on every plan.
    await _tenant_values(account, ai_image_credits=0, onboarding_free_images_remaining=3)

    # Setup finished: chat is the normal one, no longer the text-only door.
    await _finish_onboarding(account)
    chat = await account.post("/ai/chat", json={"message": "hello"})
    assert chat.status_code == 200, chat.text
    assert seen["plans"][-1] == ("free", False)
    assert (await account.get("/ai/usage")).json()["limit"] == 10
    # The unused sign-up images are for setup only: with no credits, nothing is made.
    late = await account.post("/ai/images/generate", json={**free, "use_onboarding_free": True})
    assert late.status_code == 402, late.text
    assert (await account.get("/ai/images/balance")).json()["onboarding_free_remaining"] == 3
    # ChatGPT is not part of Free.
    async with SessionLocal() as db:
        assert not await ai_plan.tenant_has_feature(db, uuid.UUID(account.tenant_id), "chatgpt")


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
    # Colours and fonts are Free's to edit, so that tool and its undo stay open.
    for change, feature in ((seo, "seo"), (fraud, "fraud")):
        _locked(await account.post(f"/ai/changes/{change['change']['id']}/undo"), feature)
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == uuid.UUID(account.site_id)))).scalar_one()
        assert site.seo.get("title_suffix") == "Before the downgrade"
        rows = (await db.execute(select(AiAgentChange.undone_at).where(AiAgentChange.tenant_id == uuid.UUID(account.tenant_id)))).scalars().all()
        assert rows and all(r is None for r in rows)
    assert (await account.post(f"/ai/changes/{theme['change']['id']}/undo")).status_code == 200

    # What the plan no longer includes is dropped from the reads, not blanked.
    async with SessionLocal() as db:
        info = await ai_tools.execute_tool("get_site_info", {}, db, uuid.UUID(account.tenant_id))
        admin = await ai_agent_admin.get_store_admin_info(db, uuid.UUID(account.tenant_id), "settings")
    assert "custom_domain" not in info and "seo_title_suffix" not in info and "google_analytics_connected" not in info
    assert "site_name" in info and "seo" not in admin and "contact" in admin

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
    async with SessionLocal() as db:
        info = await ai_tools.execute_tool("get_site_info", {}, db, uuid.UUID(account.tenant_id))
    assert "seo_title_suffix" in info and "custom_domain" in info


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
    written = await tool(account, access, "update_homepage_text", {"changes": [{"field": "tagline", "value": "Back again"}]})
    assert written["isError"] is False, written
    async with SessionLocal() as db:
        theme = (await db.execute(select(Site.theme).where(Site.id == uuid.UUID(account.site_id)))).scalar_one()
    assert theme["tagline"] == "Back again"

    await _set_plan(account, "free")
    gone = await account.delete(f"/oauth/connections/{connections.json()[0]['id']}")
    assert gone.status_code == 204


# --- the sign-up chat door ------------------------------------------------------------

class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.n, self.fail = 0, fail

    async def incr(self, key):
        if self.fail:
            raise ConnectionError("down")
        self.n += 1
        return self.n

    async def expire(self, key, seconds):
        return True


class _Reply:
    status_code = 200

    def __init__(self, parts):
        self._parts = parts

    def json(self):
        return {"candidates": [{"content": {"parts": self._parts}}]}


async def _noop(*a, **kw):
    return None


async def test_the_chat_door_is_text_only_capped_and_fails_closed(monkeypatch):
    bodies: list = []

    async def fake_post(client, url, *, params, json):
        bodies.append(json)
        # The model asks for a tool anyway; it must not run.
        return _Reply([{"functionCall": {"name": "search_customers", "args": {}}}, {"text": "Use a warm tone."}])

    async def boom(*a, **kw):
        raise AssertionError("a tool ran for the sign-up door")

    redis = _FakeRedis()
    monkeypatch.setattr(ai, "_post_gemini", fake_post)
    monkeypatch.setattr(ai_tools, "execute_tool", boom)
    monkeypatch.setattr(ai.cache, "client", lambda: redis)
    tenant = str(uuid.uuid4())

    reply, used, action = await ai.chat_reply("describe a bag", [], tenant, None, "free", text_only=True)
    assert reply == "Use a warm tone." and used == [] and action is None
    assert "tools" not in bodies[0]

    # A paying plan still gets its tools.
    monkeypatch.setattr(ai, "_check_chat_access", _noop)
    monkeypatch.setattr(ai_tools, "execute_tool", _noop)
    await ai.chat_reply("hi", [], tenant, None, "starter")
    assert "tools" in bodies[1]

    redis.n = ai.ONBOARDING_CHAT_PER_DAY
    with pytest.raises(HTTPException) as exc:
        await ai.chat_reply("one more", [], tenant, None, "free", text_only=True)
    assert exc.value.status_code == 429
    message = exc.value.detail["message"].lower()
    assert "tomorrow" in message and "buy" not in message and "credit" not in message

    # Redis down: this door alone fails closed.
    monkeypatch.setattr(ai.cache, "client", lambda: _FakeRedis(fail=True))
    with pytest.raises(HTTPException) as exc:
        await ai.chat_reply("hi", [], tenant, None, "free", text_only=True)
    assert exc.value.status_code == 503


async def test_the_setup_chat_door_closes_after_a_week(account_factory, monkeypatch):
    from datetime import UTC, datetime, timedelta

    account = await account_factory("free")
    doors: list = []

    async def fake_chat(message, history, tenant_id, db, plan, text_only=False):
        doors.append(text_only)
        return "ok", [], None

    monkeypatch.setattr(ai, "chat_reply", fake_chat)
    assert (await account.post("/ai/chat", json={"message": "hi"})).status_code == 200
    await _tenant_values(account, created_at=datetime.now(UTC) - timedelta(days=8))
    assert (await account.post("/ai/chat", json={"message": "hi"})).status_code == 200
    assert doors == [True, False]


async def test_free_ai_reports_no_more_than_the_dashboard(account_factory):
    from datetime import UTC, datetime, timedelta

    from app import basic_plan
    from app.models import Order

    account = await account_factory("starter")
    tid, sid = uuid.UUID(account.tenant_id), uuid.UUID(account.site_id)
    old = basic_plan.order_window_start() - timedelta(days=3)
    async with SessionLocal() as db:
        for number, when in (("NEW-1", datetime.now(UTC) - timedelta(days=1)), ("OLD-1", old)):
            db.add(Order(tenant_id=tid, site_id=sid, order_number=number, status="pending", subtotal_cents=500,
                         total_cents=500, currency="BDT", customer={"name": "Buyer"}, created_at=when))
        await db.commit()

    async def ask(name: str, **args) -> dict:
        async with SessionLocal() as db:
            return await ai_tools.execute_tool(name, args, db, tid)

    paid = await ask("get_sales_summary", days=365)
    assert paid["days"] == 365 and paid["order_count"] == 2 and "top_products" in paid
    assert "orders_all_time" in await ask("get_business_overview")

    await _set_plan(account, "free")
    free = await ask("get_sales_summary", days=365)
    assert free["days"] == 7 and free["order_count"] == 1 and "top_products" not in free
    overview = await ask("get_business_overview")
    assert overview["orders_last_7_days"] == 1 and "orders_all_time" not in overview
    assert [o["order_number"] for o in (await ask("list_orders"))["orders"]] == ["NEW-1"]


# --- refunds and settlement -----------------------------------------------------------

async def test_a_failed_generation_gives_back_the_credit_and_the_free_image(account, monkeypatch):
    fail = {"art": False, "image": False}

    async def art(*a, **kw):
        if fail["art"]:
            raise HTTPException(502, "art director down")
        return "brief"

    async def image(prompt, tier, reference_images, aspect_ratio):
        if fail["image"]:
            raise HTTPException(503, "Image generation failed")
        return b"img", "image/png"

    monkeypatch.setattr(images_api, "art_direct", art)
    monkeypatch.setattr(ai_images, "generate_image", image)
    await _tenant_values(account, ai_image_credits=1000, onboarding_free_images_remaining=3)
    paid = {"prompt": "a calm banner", "tier": "premium", "include_text": False}
    free = {"prompt": "a calm banner", "tier": "standard", "include_text": False, "use_onboarding_free": True}

    async def state():
        body = (await account.get("/ai/images/balance")).json()
        return body["balance"], body["onboarding_free_remaining"]

    for stage in ("art", "image"):
        fail.update(art=stage == "art", image=stage == "image")
        assert (await account.post("/ai/images/generate", json=paid)).status_code >= 500
        assert await state() == (1000, 3), f"paid path did not refund when {stage} failed"
        assert (await account.post("/ai/images/generate", json=free)).status_code >= 500
        assert await state() == (1000, 3), f"free path did not release when {stage} failed"


async def test_a_purchase_submitted_before_the_downgrade_still_settles(account_factory, monkeypatch):
    account = await account_factory("starter")

    async def noop(*a, **kw):
        return True

    monkeypatch.setattr(mailer, "send_email", noop)
    monkeypatch.setattr(notifications, "notify", noop)
    await _tenant_values(account, chat_credits=0, ai_image_credits=0)
    await _set_plan(account, "free")
    async with SessionLocal() as db:
        for kind, credits, trx in (("image_credits", 7, "TRXIMG1"), ("chat_credits", 9, "TRXCHAT1")):
            claim = PaymentClaim(
                tenant_id=uuid.UUID(account.tenant_id), kind=kind, pack_id="starter", credits=credits,
                amount_cents=10000, sender_number="01700000000", trx_id=trx,
            )
            out = await claims.apply_verified_claim(db, claim)
            assert out["ok"] and out["credits"] == credits
        tenant = (await db.execute(select(Tenant).where(Tenant.id == uuid.UUID(account.tenant_id)))).scalar_one()
        assert (tenant.ai_image_credits, tenant.chat_credits) == (7, 9)
