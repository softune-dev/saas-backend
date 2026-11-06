"""The MCP server behind the ChatGPT plugin.

MCP (Model Context Protocol) is the standard way an AI app like ChatGPT calls
tools on another service. ChatGPT sends JSON-RPC 2.0 messages to POST /mcp;
this module answers them. It is stateless (every request stands alone, JSON
replies only), which is the mode OpenAI's quickstart uses and keeps this
trivially safe to scale.

The tools are the SAME functions our own dashboard agent uses
(app/ai_tools.py reads, app/ai_agent_tools.py writes), so a store behaves
identically whichever AI drives it, tenant isolation included: the tenant
always comes from the verified token, never from tool arguments.

What is deliberately NOT exposed, and why:
  * Anything destructive or hard to undo: deleting, refunds, order status,
    replacing every category. Text a shopper typed (a name, an order note)
    could try to steer an AI into misusing a tool, so a connected AI only
    gets actions that have an Undo.
  * Billing, plans and upgrade prompts: OpenAI's plugin rules forbid showing
    plans or promoting upgrades, so limit errors are rewritten without them.
  * Customer phone numbers, fraud data, courier and payment details.

Annotations follow OpenAI's guidelines: a tool that overwrites existing data
is destructiveHint=true even though we can undo it ("being able to undo does
not justify false"), which makes ChatGPT confirm with the merchant first.
"""

import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_agent_media, ai_agent_products, ai_agent_tools, ai_agent_undo, ai_log, ai_tools, cache, mcp_images, oauth_server
from app.config import settings
from app.models import AiAgentChange, OAuthToken

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

_READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True}
_ADD = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False}
_OVERWRITE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False, "idempotentHint": True}

# name -> (title, annotations, required scope)
_READ_TOOLS: dict[str, str] = {
    "get_business_overview": "Store overview",
    "list_products": "List products",
    "get_product": "Get product",
    "list_orders": "List orders",
    "get_order": "Get order",
    "get_sales_summary": "Sales summary",
    "list_categories": "List categories",
    "list_events": "List sale events",
    "get_site_info": "Store settings",
}
_WRITE_TOOLS: dict[str, tuple[str, dict]] = {
    "get_storefront_design": ("Get storefront design", _READ),
    "update_storefront_design": ("Change store design", _OVERWRITE),
    "update_homepage_text": ("Change homepage text", _OVERWRITE),
    "add_category": ("Add category", _ADD),
    "create_product": ("Add product", _ADD),
    "update_product": ("Edit product", _OVERWRITE),
    "create_discount_event": ("Create sale event", _ADD),
    "set_homepage_image": ("Set homepage image", _OVERWRITE),
    "set_category_image": ("Set category image", _OVERWRITE),
    "add_product_image": ("Add product photo", _OVERWRITE),
}
_HANDLERS = {**ai_agent_tools.HANDLERS, **ai_agent_media.HANDLERS, **ai_agent_products.HANDLERS}
_OWN_TOOLS = {
    "add_image_to_gallery": {
        "title": "Add image to media library",
        "description": (
            "Save an image the merchant attached or that was generated in this chat into the store's media library. "
            "Returns an image_url to then place on the store with set_homepage_image, set_category_image or add_product_image. "
            "For a hero image, generate a clean background photo with NO text, buttons, logo or navigation in it (the store adds its own text on top)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "object",
                    "description": "The image file (provided by ChatGPT).",
                    # OpenAI only treats this as a file when all four properties are
                    # declared, with just the first two required.
                    "properties": {
                        "download_url": {"type": "string", "description": "Temporary download link for the file."},
                        "file_id": {"type": "string", "description": "ChatGPT's id for the file."},
                        "mime_type": {"type": "string"},
                        "file_name": {"type": "string"},
                    },
                    "required": ["download_url", "file_id"],
                },
            },
            "required": ["image"],
        },
        "annotations": _ADD,
        # Tells ChatGPT to pass the file itself (a temporary download link) for this argument.
        "_meta": {"openai/fileParams": ["image"]},
        "scope": "store.write",
    },
    "list_recent_changes": {
        "title": "List recent changes",
        "description": "List the changes made to this store through connected apps (newest first) with their ids, so one can be undone.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": _READ,
    },
    "undo_change": {
        "title": "Undo a change",
        "description": "Revert one change made through a connected app, by the id from list_recent_changes or from the change's own result. Restores what it overwrote.",
        "inputSchema": {"type": "object", "properties": {"change_id": {"type": "string"}}, "required": ["change_id"]},
        "annotations": _OVERWRITE,
    },
}

_UPGRADE_SENTENCE = re.compile(r"\s*[^.]*\b[Uu]pgrade\b[^.]*\.?")


def _json_schema(node: Any) -> Any:
    """Gemini-style schemas ("OBJECT", "STRING") -> JSON Schema ("object")."""
    if isinstance(node, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _json_schema(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_json_schema(v) for v in node]
    return node


def _tool_defs() -> list[dict]:
    decls = {
        d["name"]: d
        for d in ai_tools.TOOL_DECLARATIONS + ai_agent_tools.DECLARATIONS + ai_agent_media.DECLARATIONS + ai_agent_products.DECLARATIONS
    }
    out: list[dict] = []
    for name, title in _READ_TOOLS.items():
        out.append(_entry(decls[name], title, _READ, "store.read"))
    for name, (title, annotations) in _WRITE_TOOLS.items():
        scope = "store.read" if annotations is _READ else "store.write"
        out.append(_entry(decls[name], title, annotations, scope))
    for name, spec in _OWN_TOOLS.items():
        scope = spec.get("scope") or ("store.read" if spec["annotations"] is _READ else "store.write")
        entry = {
            "name": name, "title": spec["title"], "description": spec["description"],
            "inputSchema": spec["inputSchema"], "annotations": spec["annotations"],
            "securitySchemes": [{"type": "oauth2", "scopes": [scope]}],
        }
        if spec.get("_meta"):
            entry["_meta"] = spec["_meta"]
        out.append(entry)
    return out


def _entry(decl: dict, title: str, annotations: dict, scope: str) -> dict:
    return {
        "name": decl["name"], "title": title, "description": decl["description"],
        "inputSchema": _json_schema(decl.get("parameters") or {"type": "OBJECT", "properties": {}}),
        "annotations": annotations,
        # Both the per-tool declaration and the runtime challenge are needed
        # before ChatGPT shows its account-linking prompt.
        "securitySchemes": [{"type": "oauth2", "scopes": [scope]}],
    }


TOOL_DEFS = _tool_defs()
_TOOL_SCOPE = {t["name"]: t["securitySchemes"][0]["scopes"][0] for t in TOOL_DEFS}


# --- limits ------------------------------------------------------------------------

async def _within_limits(tenant_id: uuid.UUID, is_write: bool) -> bool:
    """Per-store ceilings so one connection (or a looping model) can't hammer
    the database. Fails open if Redis is down, like every limiter here."""
    try:
        c = cache.client()
        minute = f"mcp:calls:{tenant_id}:{int(time.time() // 60)}"
        n = await c.incr(minute)
        if n == 1:
            await c.expire(minute, 90)
        if n > settings.mcp_calls_per_minute:
            return False
        if is_write:
            hour = f"mcp:writes:{tenant_id}:{int(time.time() // 3600)}"
            w = await c.incr(hour)
            if w == 1:
                await c.expire(hour, 3700)
            if w > settings.mcp_writes_per_hour:
                return False
    except Exception as exc:  # noqa: BLE001
        log.warning("mcp rate limit check failed (allowing): %s", exc)
    return True


# --- tool execution ----------------------------------------------------------------

def _clean_error(message: str) -> str:
    """OpenAI's plugin rules forbid showing plans or promoting upgrades, so a
    plan-limit message ("...Upgrade to add more.") loses that sentence."""
    cleaned = _UPGRADE_SENTENCE.sub("", message).strip()
    return cleaned or "That isn't available for this store right now."


def _shape_write(result: dict) -> dict:
    """A write's result without the heavy parts only our dashboard needs (the
    whole theme, the template key), keeping what the model must relay."""
    change = result.get("change") or {}
    out = {k: v for k, v in result.items() if k not in ("change", "ok")}
    out["ok"] = True
    if change:
        out["summary"] = change.get("summary")
        out["change_id"] = change.get("id")
        if change.get("store_url"):
            out["see_it_live"] = change["store_url"]
        out["undo"] = "To revert this, call undo_change with change_id."
    return out


async def _recent_changes(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    rows = (
        await db.execute(
            select(AiAgentChange).where(AiAgentChange.tenant_id == tenant_id)
            .order_by(AiAgentChange.created_at.desc()).limit(20)
        )
    ).scalars().all()
    return {"changes": [{"change_id": str(r.id), "summary": r.summary, "undone": r.undone_at is not None} for r in rows]}


async def _execute(name: str, args: dict, db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    if name == "list_recent_changes":
        return await _recent_changes(db, tenant_id)
    if name == "undo_change":
        try:
            out = await ai_agent_undo.undo(db, tenant_id, uuid.UUID(str(args.get("change_id", ""))))
        except ValueError:
            return {"error": "change_id isn't valid."}
        return {"ok": True, "reverted": out.get("summary")}
    if name == "add_image_to_gallery":
        # Adds a file, never overwrites, so no Undo and no change record.
        return await mcp_images.add_image_to_gallery(db, tenant_id, args.get("image"))
    if name in _READ_TOOLS:
        return await ai_tools.execute_tool(name, args, db, tenant_id)
    result = await ai_agent_tools.run(name, args, db, tenant_id, _HANDLERS)
    return result if "error" in result else _shape_write(result)


def _challenge(error: str, description: str) -> dict:
    meta = oauth_server.issuer() + "/.well-known/oauth-protected-resource"
    return {
        "mcp/www_authenticate": [
            f'Bearer resource_metadata="{meta}", error="{error}", error_description="{description}"'
        ]
    }


def _tool_result(payload: dict, *, is_error: bool = False, meta: dict | None = None) -> dict:
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
        "structuredContent": payload,
        "isError": is_error,
    }
    if meta:
        out["_meta"] = meta
    return out


async def call_tool(db: AsyncSession, token: OAuthToken | None, name: str, args: Any) -> dict:
    if name not in _TOOL_SCOPE:
        return _tool_result({"error": f"Unknown tool {name}."}, is_error=True)
    if token is None:
        # Not signed in. This answer (with the challenge in _meta) is what makes
        # ChatGPT show its "connect your account" prompt; an HTTP 401 alone does not.
        return _tool_result(
            {"error": "Sign in to SoftuneBD to use this."}, is_error=True,
            meta=_challenge("insufficient_scope", "You need to login to continue"),
        )
    args = args if isinstance(args, dict) else {}
    needed = _TOOL_SCOPE[name]
    if needed not in token.scope.split():
        return _tool_result(
            {"error": "This connection isn't allowed to do that."}, is_error=True,
            meta=_challenge("insufficient_scope", "Reconnect SoftuneBD and allow changes to continue"),
        )
    is_write = needed == "store.write"
    if not await _within_limits(token.tenant_id, is_write):
        return _tool_result({"error": "Too many requests for this store just now. Wait a minute and try again."}, is_error=True)

    started = time.monotonic()
    tracker = ai_log.Tracker(
        kind="chat", tenant_id=token.tenant_id, user_id=token.user_id,
        input={"tool": name, "args": args},
        meta={"source": "chatgpt", "tool": name},
    )
    try:
        result = await _execute(name, args, db, token.tenant_id)
    except HTTPException as exc:
        await db.rollback()
        result = {"error": _clean_error(str(exc.detail))}
    except Exception:  # noqa: BLE001 - one broken tool must not break the connection
        log.exception("mcp tool %s failed", name)
        await db.rollback()
        result = {"error": "Something went wrong running that. Please try again."}
    if "error" in result:
        result["error"] = _clean_error(str(result["error"]))
        tracker.status = "error"
        tracker.error = result["error"][:300]
    tracker.output = {"ok": "error" not in result, "summary": result.get("summary")}
    await ai_log._write(tracker, int((time.monotonic() - started) * 1000))
    return _tool_result(result, is_error="error" in result)


# --- protocol ----------------------------------------------------------------------

def _rpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def handle(message: Any, token: OAuthToken | None, db: AsyncSession) -> dict | None:
    """One JSON-RPC message in, its reply out (None for notifications).

    `token` is None for a caller who isn't signed in. Discovery (initialize,
    ping, tools/list) works without one on purpose: ChatGPT has to read the
    tool list, and each tool's securitySchemes, BEFORE it can offer to link
    an account. The list holds no store data. Only tools/call needs a token."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid request")
    msg_id, method = message.get("id"), message.get("method")
    params = message.get("params") or {}
    if msg_id is None:
        return None  # a notification such as notifications/initialized needs no reply

    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "SoftuneBD", "version": "1.0.0"},
            "instructions": "Manage the connected SoftuneBD online store: look up products, orders and sales, "
                            "and make reversible changes to its design, homepage text, products, categories and sale events.",
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOL_DEFS}}
    if method == "tools/call":
        if not isinstance(params.get("name"), str):
            return _rpc_error(msg_id, -32602, "name is required")
        return {"jsonrpc": "2.0", "id": msg_id, "result": await call_tool(db, token, params["name"], params.get("arguments"))}
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")

