"""The AI agent behind the dashboard chat: streams its reply and acts on the
store with tools, instead of explaining where to click.

Why it replaced app/ai.py's chat_reply for the sidebar: reading every real
conversation (ai_usage_logs, Oct 2026) showed merchants asking the assistant
to DO things — recolor the store, fill in the why-choose-us section, write
the banner — and it could only reply with steps to follow. That version was
read-only, ran on Flash-Lite with a 500-token cap, wrote changes as a JSON
block pasted at the end of its text, and returned one finished blob after a
long wait. This one:
  * calls tools natively, including real writes (app/ai_agent_tools.py) that
    apply immediately and can be undone, with only destructive or customer-
    facing changes still going through a confirm card;
  * streams: text arrives as it is written, and each tool call surfaces as a
    status line ("Updating homepage text…") so the merchant sees work happen;
  * runs on a model that plans multi-step tool use (settings.ai_chat_model).

Event stream (one JSON object per SSE `data:` line) — the dashboard's
lib/api/ai.ts streamAgentChat reads exactly these:
  {"type": "status",   "tool": name, "label": text}
  {"type": "delta",    "text": chunk}
  {"type": "change",   ...}   an applied edit: id, summary, kind, [theme, template_key, store_url]
  {"type": "proposal", "action": {...}}  a confirm card (same shape as /ai/chat's pending_action)
  {"type": "done",     "tools_used": [...]}
  {"type": "error",    "message": text}
"""

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from app import ai, ai_agent_tools, ai_forms, ai_log, ai_tools
from app.config import settings
from app.db import SessionLocal

log = logging.getLogger(__name__)

_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


def _slice(text: str, start: str, end: str) -> str:
    """Reuse a section of the old chat prompt (platform knowledge, tone)
    rather than keeping two copies that drift apart."""
    i, j = text.find(start), text.find(end)
    return text[i:j].strip() if i != -1 and j > i else ""


_OLD = ai._CHAT_SYSTEM_PROMPT
_PLATFORM = _slice(_OLD, "PLATFORM KNOWLEDGE", "Most requests to change something")

# Cost discipline: this prompt and the tool list are re-sent on EVERY model
# call (2-3 calls per message), so every token here is paid ~1,000 times a
# month by a busy store. Measured Oct 2026: the first version was 6,531
# tokens per call; long platform knowledge now sits behind the
# get_platform_guide tool (fetched only for "how does X work" questions) and
# tool descriptions are trimmed to one line (_compact). Keep it short.
_AGENT_PROMPT = """You are SoftuneBD's AI agent inside a merchant's store dashboard. You make
changes yourself with tools; you are not a help-desk that explains where to click.

RULES
- If a tool can do it, DO IT. "Change my colors", "add this to why-choose-us",
  "write a better banner", "make a 20% sale", "আপনি করে দেন" = do it now. Never
  reply "go to Theme Editor..." for something a tool covers.
- Look first: get_storefront_design before design/text edits; list_products or
  list_categories before catalog edits.
- Write the copy yourself in the store's voice; don't ask the merchant to write
  it. If they just name a section ("Why choose us", "banner", "footer") or say
  "add this", look up the store (get_site_info, products), write the content
  and apply it. Ask only when a wrong guess is costly (a price, a discount %,
  which item).
- After acting, say what you changed in 1-2 short sentences. Every change has an
  Undo button, so never ask "are you sure?" for design, text or new items.
- Colors: a coherent, readable palette (black & gold = near-black surface, warm
  gold primary). Do every part of a multi-part request.
- Replacing ALL categories or changing an order status shows a confirm card.
- No tool for: connecting courier/payment accounts (merchant's own keys), domains
  (DNS), uploading photos (they use the Image tab or product page). Say so briefly.
- How-does-it-work / policy / docs questions: call get_platform_guide.
- New product: set a category only if one clearly fits; otherwise leave it empty.
- Never invent store facts (look them up) and never write reviews/testimonials.
- Reply in the merchant's language (Bangla, including Bangla typed in English
  letters, or English). Store text: the language they ask for, else match the
  store. Short, warm, plain words; no "Great question!"; numbered lists for
  steps; at most one emoji.""" + ai._softune_facts()


def _compact(decl: dict) -> dict:
    """One-line tool descriptions: the model picks tools fine from a short
    sentence, and long ones were ~half of every call's tokens."""
    out = dict(decl)
    desc = str(out.get("description", ""))
    cut = desc.find(". ")
    out["description"] = (desc[: cut + 1] if 0 < cut < 170 else desc[:170]).strip()
    params = out.get("parameters") or {}
    if params.get("properties"):
        props = {}
        for name, spec in params["properties"].items():
            spec = dict(spec)
            if "description" in spec:
                spec["description"] = str(spec["description"])[:70]
            props[name] = spec
        out["parameters"] = {**params, "properties": props}
    return out


_GUIDE_TOOL = {
    "name": "get_platform_guide",
    "description": "SoftuneBD how-to and policy knowledge: domains, SEO, payments, couriers, fraud, media, AI credits, documentation links.",
    "parameters": {"type": "OBJECT", "properties": {}},
}

TOOLS = [{"functionDeclarations": [
    _compact(d) for d in ai_tools.TOOL_DECLARATIONS + ai_agent_tools.DECLARATIONS
] + [_GUIDE_TOOL]}]

# Status lines shown while a tool runs, so the merchant sees the agent work.
_LABELS: dict[str, tuple[str, str]] = {
    "get_business_overview": ("Checking your store", "স্টোর দেখছি"),
    "list_products": ("Looking at your products", "প্রোডাক্ট দেখছি"),
    "get_product": ("Opening the product", "প্রোডাক্ট খুলছি"),
    "list_orders": ("Checking orders", "অর্ডার দেখছি"),
    "get_order": ("Opening the order", "অর্ডার খুলছি"),
    "get_sales_summary": ("Adding up sales", "বিক্রি হিসাব করছি"),
    "get_site_info": ("Reading store settings", "স্টোর সেটিংস দেখছি"),
    "get_media_stats": ("Checking storage", "স্টোরেজ দেখছি"),
    "get_billing_status": ("Checking your plan", "প্ল্যান দেখছি"),
    "list_categories": ("Looking at categories", "ক্যাটাগরি দেখছি"),
    "get_fraud_status": ("Checking fraud protection", "ফ্রড প্রোটেকশন দেখছি"),
    "list_events": ("Checking events", "ইভেন্ট দেখছি"),
    "search_customers": ("Searching customers", "কাস্টমার খুঁজছি"),
    "get_customer": ("Opening the customer", "কাস্টমার খুলছি"),
    "list_courier_connections": ("Checking couriers", "কুরিয়ার দেখছি"),
    "get_storefront_design": ("Looking at your storefront", "স্টোরফ্রন্ট দেখছি"),
    "update_storefront_design": ("Updating your design", "ডিজাইন বদলাচ্ছি"),
    "update_homepage_text": ("Updating homepage text", "হোমপেজের লেখা বদলাচ্ছি"),
    "add_category": ("Adding the category", "ক্যাটাগরি যোগ করছি"),
    "create_product": ("Adding the product", "প্রোডাক্ট যোগ করছি"),
    "update_product": ("Updating the product", "প্রোডাক্ট আপডেট করছি"),
    "create_discount_event": ("Creating the event", "ইভেন্ট তৈরি করছি"),
    "propose_replace_all_categories": ("Preparing the change", "পরিবর্তন প্রস্তুত করছি"),
    "propose_order_status": ("Preparing the change", "পরিবর্তন প্রস্তুত করছি"),
    "get_platform_guide": ("Checking the guide", "গাইড দেখছি"),
}
_BANGLA = re.compile(r"[ঀ-৿]")


def _label(tool: str, bangla: bool) -> str:
    en, bn = _LABELS.get(tool, ("Working on it", "কাজ করছি"))
    return bn if bangla else en


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _generation_config(model: str) -> dict:
    config: dict = {"temperature": 0.4, "maxOutputTokens": 2048}
    # Thinking tokens bill as output; on 2.5 models a budget caps (0 = off)
    # how much the model may "think" before answering (settings).
    if model.startswith("gemini-2.5") and settings.ai_chat_thinking_budget is not None:
        config["thinkingConfig"] = {"thinkingBudget": settings.ai_chat_thinking_budget}
    return config


async def _stream_round(
    http: httpx.AsyncClient, model: str, contents: list[dict], tracker: ai_log.Tracker
) -> AsyncIterator[dict]:
    """One model turn, streamed. Yields each response chunk's parts as they
    arrive and adds the turn's token usage to `tracker` (streamed responses
    carry usageMetadata on the chunks, so ai_log.record_gemini, which reads a
    finished response, can't be used here). Raises httpx.HTTPStatusError on a
    non-200 so the caller can fall back to the default model."""
    body = {
        "systemInstruction": {"parts": [{"text": _AGENT_PROMPT}]},
        "contents": contents,
        "tools": TOOLS,
        "generationConfig": _generation_config(model),
    }
    async with http.stream(
        "POST", _STREAM_URL.format(model=model),
        params={"key": settings.gemini_api_key, "alt": "sse"}, json=body,
    ) as res:
        if res.status_code != 200:
            await res.aread()
            res.raise_for_status()
        tracker.gemini_calls += 1
        tracker.model = model
        usage: dict = {}
        async for line in res.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usageMetadata") or usage
            for cand in chunk.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    yield part
        # usageMetadata is cumulative within a turn: the last one counts.
        # Cached prompt tokens (Gemini's implicit caching of the repeated
        # prompt+tools prefix) bill at a tenth of the input price — kept in
        # meta so the superadmin AI page can show the real cost.
        tracker.meta["cached_tokens"] = int(tracker.meta.get("cached_tokens", 0)) + int(
            usage.get("cachedContentTokenCount") or 0
        )
        tracker.prompt_tokens += int(usage.get("promptTokenCount") or 0)
        # A thinking model's reasoning tokens are billed as output but are
        # NOT in candidatesTokenCount — leaving them out under-reports cost.
        tracker.completion_tokens += int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        tracker.total_tokens += int(usage.get("totalTokenCount") or 0)


async def _run_tool(name: str, args: dict, db, tenant_id: uuid.UUID) -> dict:
    if name == "get_platform_guide":
        return {"guide": _PLATFORM}
    if name in ai_agent_tools.HANDLERS:
        return await ai_agent_tools.run(name, args, db, tenant_id)
    return await ai_tools.execute_tool(name, args, db, tenant_id)


async def run(
    *, message: str, history: list[dict], context_hint: str | None,
    tenant_id: uuid.UUID, user_id: uuid.UUID, plan: str,
) -> AsyncIterator[str]:
    """The whole agent turn as SSE strings. Opens its own DB session: a
    streaming response outlives the request-scoped one."""
    bangla = bool(_BANGLA.search(message))
    contents: list[dict] = []
    for turn in history[-10:]:
        text = str(turn.get("content", ""))[:1500]
        if text:
            contents.append({"role": "model" if turn.get("role") == "assistant" else "user", "parts": [{"text": text}]})
    # The context tag (e.g. "focus on my orders") steers tool choice but is
    # never shown back as if the merchant had typed it.
    user_text = f"{context_hint}\n\n{message}" if context_hint else message
    contents.append({"role": "user", "parts": [{"text": user_text[:6000]}]})

    reply_text: list[str] = []
    tools_used: list[str] = []
    changes: list[dict] = []
    proposal: dict | None = None

    # The log row is written by hand in `finally` rather than with
    # ai_log.track(): that context manager resets a ContextVar on exit, which
    # fails if a streaming generator is closed from another context (the
    # merchant closing the panel mid-reply) — and the row would be lost.
    tracker = ai_log.Tracker(
        kind="chat", tenant_id=tenant_id, user_id=user_id,
        input={"message": message, "history": history[-20:], "context_hint": context_hint},
        meta={"plan": plan, "agent": True},
    )
    started = time.monotonic()
    async with SessionLocal() as db:
        try:
            async for event in _turn(contents, db, tenant_id, bangla, tracker, reply_text, tools_used, changes):
                if event.get("type") == "proposal":
                    proposal = event["action"]
                yield _sse(event)
            if not reply_text:
                # The model can finish a turn with tool calls and no words.
                fallback = ("Done — " + changes[-1]["summary"].lower() + ".") if changes else (
                    "I couldn't finish that. Could you say it a little differently?"
                )
                reply_text.append(fallback)
                yield _sse({"type": "delta", "text": fallback})
        except Exception as exc:  # noqa: BLE001 - the stream must always end cleanly
            log.exception("agent turn failed")
            tracker.status = "error"
            tracker.error = str(exc)[:500]
            yield _sse({"type": "error", "message": "The AI agent hit a problem. Please try again."})
        finally:
            # Heavy turns (3+ tool calls or 2+ edits) cost a second credit,
            # charged after the work and never allowed to fail the reply.
            if len(tools_used) >= 3 or len(changes) >= 2:
                try:
                    await ai._check_chat_access(str(tenant_id), plan, db)
                except Exception:  # noqa: BLE001
                    pass
            tracker.output = {
                "reply": "".join(reply_text), "tools_used": tools_used,
                "changes": [{k: c.get(k) for k in ("id", "summary", "kind")} for c in changes],
                "pending_action": proposal,
            }
            await ai_log._write(tracker, int((time.monotonic() - started) * 1000))
    yield _sse({"type": "done", "tools_used": tools_used})


async def _turn(
    contents: list[dict], db, tenant_id: uuid.UUID, bangla: bool, tracker: ai_log.Tracker,
    reply_text: list[str], tools_used: list[str], changes: list[dict],
) -> AsyncIterator[dict]:
    """Model rounds until it answers without calling a tool. Yields event
    dicts; fills reply_text / tools_used / changes for the caller's log."""
    model = settings.ai_chat_model
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        for _ in range(settings.ai_chat_max_tool_rounds):
            parts: list[dict] = []
            # Text written before a tool call ("Let me check…") and text after
            # it are separate paragraphs, not one run-on sentence.
            gap = "\n\n" if reply_text and not reply_text[-1].endswith("\n") else ""
            try:
                async for part in _stream_round(http, model, contents, tracker):
                    parts.append(part)
                    if part.get("text") and not part.get("thought"):
                        text = gap + part["text"]
                        gap = ""
                        reply_text.append(text)
                        yield {"type": "delta", "text": text}
            except httpx.HTTPStatusError as exc:
                # A retired/unknown model id must not take the whole assistant
                # down: retry this round on the default model.
                if exc.response.status_code in (400, 404) and model != settings.ai_chat_fallback_model and not parts:
                    log.warning("agent model %s rejected (%s); falling back", model, exc.response.status_code)
                    model = settings.ai_chat_fallback_model
                    continue
                raise

            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                return
            # Replay the model's own turn (calls + thought signatures) before
            # the results, or Gemini rejects the next request.
            contents.append({"role": "model", "parts": parts})
            responses = []
            for call in calls:
                name, args = call["name"], call.get("args") or {}
                tools_used.append(name)
                yield {"type": "status", "tool": name, "label": _label(name, bangla)}
                if name in ai_agent_tools.PROPOSAL_TOOLS:
                    action = await ai_forms.attach_form(ai_agent_tools.proposal_for(name, args), db, tenant_id)
                    yield {"type": "proposal", "action": action}
                    result: dict = {"ok": True, "shown_to_merchant": "A confirm card is on screen; nothing changes until they confirm."}
                else:
                    result = await _run_tool(name, args, db, tenant_id)
                    change = result.get("change") if isinstance(result, dict) else None
                    if change:
                        changes.append(change)
                        yield {"type": "change", **change}
                        # The model doesn't need the whole theme echoed back.
                        result = {**result, "change": {"id": change["id"], "summary": change["summary"]}}
                response = {"name": name, "response": result}
                if "id" in call:
                    response["id"] = call["id"]
                responses.append({"functionResponse": response})
            contents.append({"role": "user", "parts": responses})
