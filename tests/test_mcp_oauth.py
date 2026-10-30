"""ChatGPT plugin: the OAuth flow and the MCP server.

Most of this is security, so most of it is "does the wrong thing get refused".
The first group needs no database; the second runs the whole flow through the
real endpoints with throwaway tenants (the usual `account` fixtures).
"""

import base64
import hashlib
import json
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

from app import mcp_server, oauth_server
from app.db import SessionLocal
from app.models import OAuthToken, Site

REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
RESOURCE = oauth_server.mcp_resource()


# --- no database ------------------------------------------------------------------

def test_every_tool_has_explicit_boolean_annotations_and_a_scope():
    for tool in mcp_server.TOOL_DEFS:
        a = tool["annotations"]
        assert all(isinstance(a[k], bool) for k in ("readOnlyHint", "destructiveHint", "openWorldHint")), tool["name"]
        assert tool["description"].strip() and tool["securitySchemes"][0]["type"] == "oauth2"
        assert tool["inputSchema"]["type"] == "object"


def test_overwriting_tools_are_marked_destructive_and_reads_are_read_only():
    by = {t["name"]: t["annotations"] for t in mcp_server.TOOL_DEFS}
    for name in ("update_storefront_design", "update_homepage_text", "update_product", "undo_change"):
        assert by[name]["destructiveHint"] is True and by[name]["readOnlyHint"] is False, name
    for name in ("add_category", "create_product", "create_discount_event"):
        assert by[name]["destructiveHint"] is False and by[name]["readOnlyHint"] is False, name
    for name in ("get_business_overview", "list_products", "list_orders", "get_storefront_design"):
        assert by[name]["readOnlyHint"] is True, name


def test_no_billing_customer_pii_or_destructive_tools_are_exposed():
    names = {t["name"] for t in mcp_server.TOOL_DEFS}
    forbidden = {"get_billing_status", "search_customers", "get_customer", "get_fraud_status",
                 "list_courier_connections", "get_media_stats", "propose_replace_all_categories",
                 "propose_order_status"}
    assert not (names & forbidden)
    assert not any(n.startswith(("delete_", "remove_", "refund")) for n in names)


def test_schema_types_are_json_schema_not_gemini_uppercase():
    blob = json.dumps(mcp_server.TOOL_DEFS)
    assert '"OBJECT"' not in blob and '"STRING"' not in blob


def test_upgrade_promotions_are_stripped_from_errors():
    msg = "Product limit reached (50 on your current plan). Upgrade to add more products."
    cleaned = mcp_server._clean_error(msg)
    assert "pgrade" not in cleaned and "Product limit reached" in cleaned


def test_pkce_and_redirect_rules():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert oauth_server.pkce_ok(verifier, challenge)
    assert not oauth_server.pkce_ok(secrets.token_urlsafe(48), challenge)
    assert not oauth_server.pkce_ok("short", challenge)
    assert oauth_server.redirect_allowed(REDIRECT)
    assert oauth_server.redirect_allowed("https://chatgpt.com/connector/oauth/abc_123")
    for bad in ("https://evil.example/cb", "https://chatgpt.com.evil.example/connector/oauth/a",
                "https://chatgpt.com/connector/oauth/a/../b", "http://chatgpt.com/connector/oauth/a"):
        assert not oauth_server.redirect_allowed(bad), bad


def test_approval_request_token_cannot_be_used_as_a_login_token():
    import asyncio

    from app.security import decode_token

    token = oauth_server.make_request_token({"client_id": "x", "scope": "store.read"})
    with pytest.raises(Exception) as exc:
        asyncio.run(decode_token(token, "access"))
    assert "401" in str(exc.value) or getattr(exc.value, "status_code", None) == 401


# --- the whole flow, against the real endpoints ----------------------------------

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


async def connect(account, scope: str = "store.read store.write") -> dict:
    """ChatGPT's side of the flow plus the merchant's approval; returns tokens."""
    http = account.client
    reg = await http.post("/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": [REDIRECT]})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "s123",
              "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE, "scope": scope}
    auth = await http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert auth.status_code == 302, auth.text
    request_token = parse_qs(urlsplit(auth.headers["location"]).query)["request"][0]

    preview = await account.get("/oauth/consent", params={"request": request_token})
    assert preview.status_code == 200 and preview.json()["permissions"]
    consent = await account.post("/oauth/consent", json={"request": request_token, "approve": True})
    assert consent.status_code == 200, consent.text
    back = parse_qs(urlsplit(consent.json()["redirect_url"]).query)
    assert back["state"] == ["s123"] and back["iss"] == [oauth_server.issuer()]
    code = back["code"][0]
    return {"client_id": client_id, "code": code, "verifier": verifier, "scope": scope}


async def exchange(account, flow: dict, **override) -> object:
    form = {"grant_type": "authorization_code", "client_id": flow["client_id"], "code": flow["code"],
            "redirect_uri": REDIRECT, "code_verifier": flow["verifier"], "resource": RESOURCE, **override}
    return await account.client.post("/oauth/token", data=form)


async def rpc(account, access: str, method: str, params: dict | None = None, msg_id: int = 1):
    return await account.client.post(
        "/mcp", headers={"Authorization": f"Bearer {access}"},
        json={"jsonrpc": "2.0", "id": msg_id, "method": method, **({"params": params} if params else {})},
    )


async def tool(account, access: str, name: str, args: dict | None = None) -> dict:
    res = await rpc(account, access, "tools/call", {"name": name, "arguments": args or {}})
    assert res.status_code == 200, res.text
    return res.json()["result"]


async def _tokens(account, scope: str = "store.read store.write") -> dict:
    flow = await connect(account, scope)
    res = await exchange(account, flow)
    assert res.status_code == 200, res.text
    return {**res.json(), "client_id": flow["client_id"]}


async def test_full_flow_then_mcp_works_and_scope_is_respected(account, site):
    tokens = await _tokens(account)
    access = tokens["access_token"]
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] > 0

    init = (await rpc(account, access, "initialize", {"protocolVersion": "2025-06-18"})).json()["result"]
    assert init["serverInfo"]["name"] == "SoftuneBD" and "tools" in init["capabilities"]
    listed = (await rpc(account, access, "tools/list")).json()["result"]["tools"]
    assert len(listed) == len(mcp_server.TOOL_DEFS)

    overview = await tool(account, access, "get_business_overview")
    assert overview["isError"] is False

    # A store write through the plugin, then undo it with the id it returned.
    changed = await tool(account, access, "update_homepage_text", {"changes": [{"field": "tagline", "value": "Hand-made in Dhaka"}]})
    assert changed["isError"] is False and changed["structuredContent"]["change_id"]
    async with SessionLocal() as db:
        theme = (await db.execute(select(Site.theme).where(Site.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
    assert theme["tagline"] == "Hand-made in Dhaka"
    undone = await tool(account, access, "undo_change", {"change_id": changed["structuredContent"]["change_id"]})
    assert undone["isError"] is False


async def test_read_only_connection_cannot_write(account, site):
    tokens = await _tokens(account, "store.read")
    result = await tool(account, tokens["access_token"], "update_homepage_text", {"changes": [{"field": "tagline", "value": "nope"}]})
    assert result["isError"] is True
    assert "insufficient_scope" in json.dumps(result["_meta"])
    async with SessionLocal() as db:
        theme = (await db.execute(select(Site.theme).where(Site.tenant_id == uuid.UUID(account.tenant_id)))).scalars().first()
    assert (theme or {}).get("tagline") != "nope"


async def test_discovery_is_public_but_tool_calls_need_a_sign_in(account):
    http = account.client
    ping = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    listed = await http.post("/mcp", json=ping)
    assert listed.status_code == 200 and "resource_metadata" in listed.headers["www-authenticate"]
    tools = listed.json()["result"]["tools"]
    assert tools and all(t["securitySchemes"][0]["type"] == "oauth2" for t in tools)

    call = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                         "params": {"name": "get_business_overview", "arguments": {}}})
    result = call.json()["result"]
    assert result["isError"] is True and "mcp/www_authenticate" in result["_meta"]


async def test_a_presented_but_invalid_token_is_401(account):
    res = await account.client.post("/mcp", headers={"Authorization": "Bearer not-a-real-token"},
                                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert res.status_code == 401 and "resource_metadata" in res.headers["www-authenticate"]


async def test_wrong_pkce_verifier_is_refused(account):
    flow = await connect(account)
    res = await exchange(account, flow, code_verifier=secrets.token_urlsafe(48))
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"


async def test_replaying_a_code_fails_and_revokes_what_it_issued(account):
    flow = await connect(account)
    first = await exchange(account, flow)
    assert first.status_code == 200
    second = await exchange(account, flow)
    assert second.status_code == 400
    after = await rpc(account, first.json()["access_token"], "ping")
    assert after.status_code == 401, "a replayed code means theft: the first token must stop working"


async def test_refresh_rotates_and_reusing_the_old_refresh_token_kills_the_connection(account):
    tokens = await _tokens(account)
    form = {"grant_type": "refresh_token", "client_id": tokens["client_id"]}
    new = await account.client.post("/oauth/token", data={**form, "refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert (await rpc(account, new.json()["access_token"], "ping")).status_code == 200
    assert (await rpc(account, tokens["access_token"], "ping")).status_code == 401  # old pair retired

    reuse = await account.client.post("/oauth/token", data={**form, "refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 400
    assert (await rpc(account, new.json()["access_token"], "ping")).status_code == 401  # whole connection revoked


async def test_expired_and_revoked_access_tokens_are_refused(account):
    tokens = await _tokens(account)
    async with SessionLocal() as db:
        await db.execute(update(OAuthToken).where(OAuthToken.tenant_id == uuid.UUID(account.tenant_id))
                         .values(access_expires_at=OAuthToken.created_at))
        await db.commit()
    assert (await rpc(account, tokens["access_token"], "ping")).status_code == 401

    fresh = await _tokens(account)
    assert (await rpc(account, fresh["access_token"], "ping")).status_code == 200
    await account.client.post("/oauth/revoke", data={"token": fresh["access_token"]})
    assert (await rpc(account, fresh["access_token"], "ping")).status_code == 401


async def test_unknown_redirect_uri_is_an_error_page_never_a_redirect(account):
    reg = await account.client.post("/oauth/register", json={"redirect_uris": [REDIRECT]})
    _, challenge = _pkce()
    res = await account.client.get("/oauth/authorize", follow_redirects=False, params={
        "response_type": "code", "client_id": reg.json()["client_id"], "redirect_uri": "https://evil.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE})
    assert res.status_code == 400 and "location" not in res.headers


async def test_registering_a_foreign_redirect_uri_is_refused(account):
    res = await account.client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]})
    assert res.status_code == 400 and res.json()["error"] == "invalid_redirect_uri"


async def test_a_token_only_ever_sees_its_own_store(two_accounts):
    a, b = two_accounts
    token_a = (await _tokens(a))["access_token"]
    token_b = (await _tokens(b))["access_token"]
    # Each connection reads ITS OWN recent changes: A's change must be invisible to B.
    await tool(a, token_a, "update_homepage_text", {"changes": [{"field": "tagline", "value": "A only"}]})
    seen_by_b = await tool(b, token_b, "list_recent_changes")
    seen_by_a = await tool(a, token_a, "list_recent_changes")
    assert seen_by_b["structuredContent"]["changes"] == []
    assert len(seen_by_a["structuredContent"]["changes"]) == 1


async def test_another_store_cannot_disconnect_my_app(two_accounts):
    a, b = two_accounts
    await _tokens(a)
    connections = (await a.get("/oauth/connections")).json()
    assert len(connections) == 1
    assert (await b.delete(f"/oauth/connections/{connections[0]['id']}")).status_code == 404  # never 403
    assert (await a.delete(f"/oauth/connections/{connections[0]['id']}")).status_code == 204
    assert (await a.get("/oauth/connections")).json() == []
