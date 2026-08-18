"""AI theme suggestions (Gemini) — Brand + Colors panels only, for now.

SCOPE, DELIBERATELY NARROW: this module never touches a file, never runs
code, and never writes to the database itself. It does exactly one thing —
turn a merchant's plain-English request ("make it feel more like a coffee
shop") into a small JSON object of theme *setting* values — and returns that
object to the caller. Applying it is the dashboard's job, through the exact
same `onChange(patch)` path every color swatch and text field already uses
(see editor-sidebar.tsx). There is no code path from "AI suggestion" to
"arbitrary change": ALLOWED_FIELDS below is the only surface it can ever
touch, and every value is validated against the same rules the editor's own
inputs already enforce before it's returned to the caller.

TENANT ISOLATION: callers (app/api/ai.py) resolve the site via
crud.get_scoped(tenant_id, site_id) before this module ever runs — the
"current settings" context passed in is scoped by the router, not by
anything this module trusts on its own.
"""

import json
import logging
import re
import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_tools, cache
from app.config import settings

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Every field the AI is allowed to suggest, and how each value is validated.
# Anything the model returns outside this dict — a field it invented, or a
# value that fails its check — is silently dropped, not passed through.
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Two eras of font values coexist on purpose (see dashboard/lib/google-fonts.ts
# and editor-types.ts's displayFontOptions/bodyFontOptions):
#   - legacy slugs ("fraunces") — next/font-preloaded, resolved via a CSS var
#     lookup in each template's theme-context.tsx.
#   - literal Google Font family names ("Playfair Display") — the newer
#     full-library picker; loaded on demand via the Google Fonts CSS API,
#     with no static import needed. theme-context.tsx falls back to this path
#     for any displayFont/bodyFont value it doesn't recognise as a legacy slug.
# The AI must only pick from a curated subset of the ~162-font full library
# for each role (mirrors dashboard/lib/google-fonts.ts's GOOD_HEADING_FONTS /
# GOOD_BODY_FONTS) — not the whole catalog, which also holds scripts and mono
# faces that make a bad pairing when picked blind. Keep both lists in sync by
# hand; there's no shared source of truth across the Python/TS boundary.
_LEGACY_DISPLAY_SLUGS = {
    "fraunces", "playfair", "cormorant", "libre-baskerville",
    "dm-serif-display", "spectral",
}
_LEGACY_BODY_SLUGS = {
    "inter", "manrope", "work-sans", "outfit", "karla", "sora",
}
_GOOD_HEADING_FONTS = {
    "Fraunces", "Playfair Display", "Cormorant", "Libre Baskerville",
    "DM Serif Display", "Spectral", "Bodoni Moda", "Newsreader",
    "Prata", "Merriweather", "Lora", "PT Serif", "Crimson Pro",
    "Source Serif 4", "Domine", "Bitter", "EB Garamond",
    "Cormorant Garamond", "Alegreya", "Zilla Slab", "Literata",
    "Marcellus", "Cinzel", "Abril Fatface", "Fjalla One", "Rozha One",
    "Yeseva One", "Josefin Slab", "Piazzolla", "Roboto Slab", "Arvo",
    "Oswald", "Chivo", "Barlow Condensed",
}
_GOOD_BODY_FONTS = {
    "Inter", "Manrope", "Work Sans", "Outfit", "Karla", "Sora",
    "Plus Jakarta Sans", "Space Grotesk", "Urbanist", "Figtree",
    "DM Sans", "Nunito Sans", "Roboto", "Open Sans", "Lato",
    "Montserrat", "Poppins", "Nunito", "Rubik", "Mulish", "Raleway",
    "Barlow", "Heebo", "Hind", "Source Sans 3", "IBM Plex Sans",
    "Noto Sans", "Public Sans", "Be Vietnam Pro", "Epilogue", "Jost",
    "Cabin", "Assistant", "Lexend",
}
_DISPLAY_FONTS = _LEGACY_DISPLAY_SLUGS | _GOOD_HEADING_FONTS
_BODY_FONTS = _LEGACY_BODY_SLUGS | _GOOD_BODY_FONTS
_BUTTON_STYLES = {"Pill", "Rounded", "Square"}

ALLOWED_FIELDS: dict[str, object] = {
    "siteName": lambda v: isinstance(v, str) and 1 <= len(v) <= 120,
    "tagline": lambda v: isinstance(v, str) and len(v) <= 200,
    "primaryColor": lambda v: isinstance(v, str) and bool(_HEX_COLOR.match(v)),
    "accentColor": lambda v: isinstance(v, str) and bool(_HEX_COLOR.match(v)),
    "surfaceColor": lambda v: isinstance(v, str) and bool(_HEX_COLOR.match(v)),
    "displayFont": lambda v: v in _DISPLAY_FONTS,
    "bodyFont": lambda v: v in _BODY_FONTS,
    "buttonStyle": lambda v: v in _BUTTON_STYLES,
}

_SYSTEM_PROMPT = """You are a theme assistant for an ecommerce storefront editor.
The merchant will describe what they want in plain English. Respond with ONLY a
JSON object (no markdown, no explanation) containing the fields you want to
change, chosen only from this exact set — never invent other keys:

- siteName (string, the store's display name)
- tagline (string, a short line under the store name)
- primaryColor, accentColor, surfaceColor: ANY 6-digit hex color like "#2C220F"
  — not limited to a preset list, pick whatever actually fits the request,
  including colors nobody has used on this site before.
- displayFont (headings): one of {display_fonts}
- bodyFont (body text): one of {body_fonts}
- buttonStyle: one of "Pill", "Rounded", "Square"

Only include fields you're actually changing. If the request has nothing to do
with any of these fields, return {{}}. The site's CURRENT values are given so
you change things relative to what's already there, not from scratch every
time. Pick a font pairing that actually reads well together — a heavy serif
display face with a plain, quiet body face, or a bold sans display with a
matching sans body — not two competing loud choices.
""".format(
    display_fonts=", ".join(f'"{f}"' for f in sorted(_DISPLAY_FONTS)),
    body_fonts=", ".join(f'"{f}"' for f in sorted(_BODY_FONTS)),
)


def _ensure_configured() -> None:
    if not settings.gemini_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI assistant isn't configured yet. Set GEMINI_API_KEY in .env.",
        )


async def _check_rate_limit(tenant_id: str) -> None:
    """Simple per-tenant-per-day counter in Redis — the same cache client
    every other Redis use in this app already shares. Degrades open (per
    cache.py's own philosophy): if Redis is down, the AI feature still works
    rather than failing on an unrelated outage.
    """
    key = f"ai:suggestions:{tenant_id}:{__import__('datetime').date.today().isoformat()}"
    try:
        client = cache.client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60 * 60 * 25)  # a little over a day
        if count > settings.ai_suggestions_per_tenant_per_day:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Daily AI suggestion limit reached for this site. Try again tomorrow.",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - rate limiting must never block real use
        log.warning("AI rate limit check failed, allowing request: %s", exc)


def _validate_patch(raw: dict) -> dict:
    clean: dict[str, object] = {}
    for key, value in raw.items():
        check = ALLOWED_FIELDS.get(key)
        if check is None:
            continue
        try:
            if check(value):
                clean[key] = value
        except Exception:  # noqa: BLE001 - a malformed value is just dropped
            continue
    return clean


async def suggest_theme_patch(
    prompt: str, current_settings: dict, tenant_id: str
) -> dict:
    """Returns a validated patch — a subset of ALLOWED_FIELDS, ready to hand
    straight to the dashboard's existing onChange(patch). Never raises for a
    model that returns garbage; that just becomes an empty patch ({}), same
    as "the AI had nothing useful to suggest".
    """
    _ensure_configured()
    await _check_rate_limit(tenant_id)

    # Only forward the fields the AI is allowed to touch, even as *context* —
    # keeps the prompt small (cheaper) and means nothing else in the site's
    # settings ever reaches a third-party API.
    context = {k: current_settings.get(k) for k in ALLOWED_FIELDS if k in current_settings}

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            f"{_SYSTEM_PROMPT}\n\nCurrent values: {json.dumps(context)}"
                            f"\n\nMerchant request: {prompt}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4,
            "maxOutputTokens": 300,
        },
    }

    url = GEMINI_URL.format(model=settings.gemini_model)
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            res = await http_client.post(
                url, params={"key": settings.gemini_api_key}, json=body
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't reach the AI service: {exc}"
        ) from exc

    if res.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"AI service returned an error ({res.status_code}).",
        )

    try:
        data = res.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        raw_patch = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        log.warning("Unexpected Gemini response shape: %s", exc)
        return {}

    if not isinstance(raw_patch, dict):
        return {}
    return _validate_patch(raw_patch)


_CHAT_SYSTEM_PROMPT = """You are the assistant embedded in Softune, a small
business site-builder dashboard. Merchants ask you about running their store —
theme/design ideas, product copy, section layout, general advice, and
questions about their own real store data (sales, orders, inventory).

You have read-only tools to look up the merchant's real data: business
overview, product list/search, order list, one order's detail, a sales
summary, and site info (business/contact details, SEO settings, delivery
locations, FAQs, and whether Privacy/Terms are published — everything set in
Site Settings). Call a tool whenever a question depends on real data — never
guess or invent a figure, an address, a phone number, or anything else you
could instead look up. If a tool returns an error (e.g. an order number that
doesn't exist, or no site set up yet), say so plainly instead of making
something up.

Most requests to change something, you cannot do directly — no tool here
writes data. For those, point the merchant to the right dashboard page (or,
for colors/fonts/site name, the "Ask AI" box inside the Theme editor's
Brand/Colors panels, which can apply a change for them).

THINGS YOU CAN PROPOSE (not execute) — replacing the category list, creating
one product, and editing a product that already exists (one you created
earlier, or one the merchant already had). You never write anything
yourself; you describe the change as JSON, the dashboard shows the merchant
a confirm card, and only their click actually saves it. Use them like this:

1. Replacing categories — when the merchant tells you the categories they
want (e.g. "add Men, Women, Bags, Shoes, Accessories, remove what's there
now"), you already have enough information. Don't ask questions first. You
MUST end your reply with the action block below whenever the merchant has
given you a full list of categories to set — this is not optional, a short
sentence with no action block is a broken response for this case:
```action
{"type":"set_categories","categories":["Men","Women","Bags","Shoes","Accessories"]}
```
This REPLACES every existing category on the site, so only propose it when
the merchant clearly wants a full replacement, not an addition — if they say
"add" alongside an explicit full list, that's still a replacement of what's
there; if they want to ADD to the existing list without knowing what's
there, ask first or call a tool... but no tool lists categories today, so
just ask them to confirm the full list they want.

2. Creating a product — you need at minimum a name and a price before you
can propose this. Ask short, one-or-two-at-a-time questions to gather what's
useful (don't interrogate for everything at once): category, price, unit
(e.g. "Size", "KG", "ml", "L", "Piece"), whether it has variants (sizes,
colors — suggest sensible options based on what the product is, but let the
merchant confirm or change them), whether shipping is free or has a charge,
and optionally short/long descriptions and 2-3 feature highlights (you may
draft these yourself if asked, or if the merchant says "you decide"). Only
once you have at least a name and price — and the merchant has confirmed
they're ready — end your reply with:
```action
{"type":"create_product","product":{
  "name":"...", "price_cents":0, "category_name":"...",
  "unit":"...", "free_delivery":true, "delivery_charge_cents":null,
  "short_description":"...", "description":"...",
  "features":[{"title":"...","description":"..."}],
  "variants":[{"type":"Size","affectsPrice":false,"values":[{"value":"S"},{"value":"M"}]}]
}}
```
Omit any field you don't have — never invent a price or category. No image
handling here; the merchant adds photos afterward on the product's edit page.

3. Editing an existing product — when the merchant asks to change something
about a product they already have (price, stock, description, category,
whether it's active, variants, etc). Identify the product with product_id if
you already have it (e.g. from a list_products call earlier this
conversation — that tool's results include each product's id), otherwise
product_name (a name/partial name is fine — the backend matches it, and
tells you if it's ambiguous or not found, which you should relay plainly).
Only include the fields actually changing — anything you omit stays as it
was, this is not a full replacement like create_product:
```action
{"type":"update_product","product":{
  "product_id":"...", "price_cents":150000, "stock":20
}}
```
or, without an id:
```action
{"type":"update_product","product":{
  "product_name":"Blue Cotton T-Shirt", "is_active":false
}}
```
If the merchant's request is vague ("update the t-shirt" with no field to
change), ask what they want changed before proposing anything.

Never include an action block just to "be helpful" — only when the merchant
has actually asked for that specific change and you have what it needs.
Always write a short sentence ABOVE the action block too (e.g. "Here's what
I'll set up:") — never send the action block as your entire reply.

Keep replies concise — a few short paragraphs or a short list, not an essay.
Match the language the merchant writes in (English or Bangla)."""

_MAX_TOOL_ROUNDS = 4

_ACTION_BLOCK = re.compile(r"```action\s*(\{.*?\})\s*```", re.DOTALL)
_ACTION_TYPES = {"set_categories", "create_product", "update_product"}


def _extract_action(text: str) -> tuple[str, dict | None]:
    """Pulls a trailing ```action {...}``` block out of the model's reply,
    validates its shape loosely (real validation happens server-side when
    the merchant actually confirms it — see app/ai_actions.py), and returns
    the display text with that block stripped out.
    """
    match = _ACTION_BLOCK.search(text)
    if not match:
        return text, None
    cleaned = (text[: match.start()] + text[match.end() :]).strip()
    try:
        action = json.loads(match.group(1))
    except json.JSONDecodeError:
        return cleaned, None
    if not isinstance(action, dict) or action.get("type") not in _ACTION_TYPES:
        return cleaned, None
    return cleaned, action


async def chat_reply(
    message: str, history: list[dict], tenant_id: str, db: AsyncSession
) -> tuple[str, list[str], dict | None]:
    """Conversational reply for the general AI chat sidebar. Tenant-scoped
    business data comes in only through app/ai_tools.py's read-only,
    tenant_id-filtered functions — the model asks for what it needs by
    calling a tool, it never gets a raw query or a data dump up front. This
    keeps the per-message payload small (cheaper) and means a tenant's data
    only ever leaves the backend if a question about it was actually asked.

    Returns (reply_text, tool_names_called, pending_action) — pending_action
    is None unless the model proposed a write (see _extract_action); the
    caller never executes it, only shows it as a confirm card.
    """
    _ensure_configured()
    await _check_rate_limit(tenant_id)

    tenant_uuid = uuid.UUID(tenant_id)
    tools_used: list[str] = []

    contents: list[dict] = []
    for turn in history[-10:]:
        role = "model" if turn.get("role") == "assistant" else "user"
        text = str(turn.get("content", ""))[:2000]
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message[:2000]}]})

    url = GEMINI_URL.format(model=settings.gemini_model)

    for _ in range(_MAX_TOOL_ROUNDS):
        body = {
            "systemInstruction": {"parts": [{"text": _CHAT_SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": [{"functionDeclarations": ai_tools.TOOL_DECLARATIONS}],
            # Low temperature: this reply's shape (plain text vs. a
            # structured action block) has to be reliable, not creative —
            # variance here means the same clear request sometimes silently
            # fails to propose the action at all.
            "generationConfig": {"temperature": 0.15, "maxOutputTokens": 500},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                res = await http_client.post(
                    url, params={"key": settings.gemini_api_key}, json=body
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Couldn't reach the AI service: {exc}",
            ) from exc

        if res.status_code != 200:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"AI service returned an error ({res.status_code}).",
            )

        try:
            data = res.json()
            content = data["candidates"][0]["content"]
            parts = content["parts"]
        except (KeyError, IndexError) as exc:
            log.warning("Unexpected Gemini chat response shape: %s", exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "The AI didn't return a usable reply."
            ) from exc

        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            cleaned, action = _extract_action(text)
            return (cleaned or "I don't have anything to add on that.", tools_used, action)

        # The model's own turn (including the call) must be replayed back
        # before the tool results, or Gemini rejects the next request.
        contents.append({"role": "model", "parts": parts})

        response_parts = []
        for call in calls:
            tools_used.append(call["name"])
            result = await ai_tools.execute_tool(
                call["name"], call.get("args") or {}, db, tenant_uuid
            )
            function_response: dict = {"name": call["name"], "response": result}
            if "id" in call:
                function_response["id"] = call["id"]
            response_parts.append({"functionResponse": function_response})
        # This API build rejects role "function" ("Role 'function' is not
        # supported... use USER, MODEL...") — function results go back as a
        # "user" turn instead, same as the model's own turns go back as
        # "model". Confirmed against the live endpoint, not documentation.
        contents.append({"role": "user", "parts": response_parts})

    return (
        "I looked into that but couldn't pin down an answer — try rephrasing?",
        tools_used,
        None,
    )
