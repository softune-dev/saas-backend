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

import asyncio
import json
import logging
import re
import uuid
from datetime import date

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_tools, cache
from app.config import settings

log = logging.getLogger(__name__)

# Daily AI request cap per tenant plan. NOT finalized pricing — deliberately
# just a dict, not a database column or migration, so it's a one-line change
# whenever the real plan lineup settles. Four real plans, no "free" tier:
#   - demo: internal/trial tenants (today's only two real tenants) — enough
#     allowance to actually try the assistant, not a token amount.
#   - starter: 0 means no AI access at all, by design, not just a low cap.
#   - growth / business: real paid-tier allowances.
# Any plan value not listed falls back to DEFAULT_AI_DAILY_CAP (same as
# growth) rather than silently 0 — an unrecognized plan should never look
# like "AI isn't included," that's what 0 explicitly means.
PLAN_AI_DAILY_CAP: dict[str, int] = {
    "demo": 50,
    "starter": 0,
    "growth": 80,
    "business": 250,
}
DEFAULT_AI_DAILY_CAP = 80


def _usage_key(tenant_id: str) -> str:
    return f"ai:suggestions:{tenant_id}:{date.today().isoformat()}"


def plan_cap(plan: str) -> int:
    return PLAN_AI_DAILY_CAP.get(plan, DEFAULT_AI_DAILY_CAP)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Gemini returns a real, well-formed 503 ("This model is currently
# experiencing high demand ... usually temporary") under normal free-tier
# load — not a bug on our end, just the model being busy for a moment. A
# short retry absorbs that instead of failing the merchant's request on the
# first hiccup; 429 (rate limit) gets the same treatment for the same reason.
_RETRY_STATUS_CODES = {429, 503}
_RETRY_DELAYS_SECONDS = [1.0, 3.0]  # two retries: ~1s then ~3s after that


async def _post_gemini(
    http_client: httpx.AsyncClient, url: str, *, params: dict, json: dict
) -> httpx.Response:
    """POST to Gemini with a short retry on transient overload/rate-limit
    responses. Returns whatever the last attempt got — callers still check
    res.status_code themselves, this only saves them from a first-touch 503."""
    res = await http_client.post(url, params=params, json=json)
    for delay in _RETRY_DELAYS_SECONDS:
        if res.status_code not in _RETRY_STATUS_CODES:
            break
        await asyncio.sleep(delay)
        res = await http_client.post(url, params=params, json=json)
    return res

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


async def _check_ai_access(tenant_id: str, plan: str) -> None:
    """Real per-tenant-per-day counter in Redis — the same cache client
    every other Redis use in this app already shares. Degrades open on a
    Redis outage (per cache.py's own philosophy) for the COUNTING part —
    an AI feature failing because caching is briefly down would be a worse
    experience than one extra request slipping through. The plan check
    itself never degrades open: a 0-cap plan is a real "not included in
    your plan" answer, not something a Redis blip should ever bypass.
    """
    cap = plan_cap(plan)
    if cap <= 0:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "AI features aren't included in your current plan. Upgrade to use the AI assistant.",
        )

    key = _usage_key(tenant_id)
    try:
        client = cache.client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60 * 60 * 25)  # a little over a day
        if count > cap:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Daily AI limit reached ({cap}/day). Try again tomorrow.",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - counting must never block real use
        log.warning("AI usage counter failed, allowing request: %s", exc)


async def get_usage(tenant_id: str, plan: str) -> dict:
    """Read-only peek at today's count — does NOT increment. Powers the
    dashboard header's credits display; must never itself consume a
    request just by being looked at.
    """
    cap = plan_cap(plan)
    used = 0
    try:
        raw = await cache.client().get(_usage_key(tenant_id))
        used = int(raw) if raw else 0
    except Exception as exc:  # noqa: BLE001 - a display glitch, not a hard failure
        log.warning("AI usage lookup failed: %s", exc)
    return {"used": used, "limit": cap, "remaining": max(0, cap - used)}


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
    prompt: str, current_settings: dict, tenant_id: str, plan: str
) -> dict:
    """Returns a validated patch — a subset of ALLOWED_FIELDS, ready to hand
    straight to the dashboard's existing onChange(patch). Never raises for a
    model that returns garbage; that just becomes an empty patch ({}), same
    as "the AI had nothing useful to suggest".
    """
    _ensure_configured()
    await _check_ai_access(tenant_id, plan)

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
            res = await _post_gemini(
                http_client, url, params={"key": settings.gemini_api_key}, json=body
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

TONE — you're a capable, genuinely helpful colleague who happens to know this
merchant's store inside out, not a customer-support script, and not a
spreadsheet reading its own report out loud. Warm and human on plain
greetings ("Hi", "Hello") — a short, genuine welcome before anything else.
Most merchants using this are not technical — write like you're texting a
shop owner friend who's busy, not filing a report: everyday words over
business-report words ("what's selling", "how much you made" over "revenue
performance", "inventory status"), and skip section-label headings like
"Last 30 days:" / "All time:" / "Inventory:" — say it as sentences instead of
labeled fields. Once they ask something real, be direct: lead with the
answer, skip preamble like "Great question!" or "I'd be happy to help!", and
don't pad a short answer into a long one just to seem thorough. It's fine to
notice something worth flagging (e.g. a product nearly out of stock, an order
queue piling up) even if they didn't ask, but don't turn every reply into an
unsolicited audit.

One or two emoji MAX per reply, only where a genuinely warm or celebratory
moment earns one (a friendly hello, real good news like a strong sales week)
— never one per line, never on routine facts, never as decoration. Zero
emoji is correct far more often than one.

FORMATTING — plain sentences for anything short. For a genuine multi-item
breakdown (several numbers, several steps), use a numbered list (`1.`, `2.`),
not asterisk bullets — numbered reads cleaner in this chat and makes items
easy to refer back to ("about #2..."). Bold (`**like this**`) only for a
number or term that's genuinely the point of the sentence, not whole phrases.
Never use `*` as a bullet marker.

You have read-only tools to look up the merchant's real data: business
overview, product list/search, order list, one order's detail, a sales
summary, site info (domain, business/contact details, full SEO
configuration, delivery locations, FAQs, About Us, and whether Privacy/Terms
are published — everything set in Site Settings), media storage usage, and
billing/plan status. Call a tool whenever a question depends on real data —
never guess or invent a figure, an address, a phone number, or anything else
you could instead look up. If a tool returns an error (e.g. an order number
that doesn't exist, or no site set up yet), say so plainly instead of making
something up.

DIAGNOSE, DON'T JUST REPORT. When you call get_site_info, get_media_stats, or
get_billing_status, actually look at what came back for real gaps worth
mentioning — don't just read values aloud. Real examples of what's worth
flagging: no meta description set (hurts how the store shows up in Google),
no OG image (a shared link looks broken on WhatsApp/Facebook), no custom
domain connected, indexing hidden on a store that's actually publishing
products, no FAQs on a store with real inventory, storage past ~80% of the
plan limit, today's AI usage near the daily cap. Only mention what's
genuinely relevant to what they asked or what you were already looking up —
don't bolt an unrelated audit onto a one-line question. And only flag a gap
once per conversation; don't repeat the same nudge every time related data
comes up again.

PLATFORM KNOWLEDGE — what Softune actually has today, so you can answer "how
do I..." and "what is..." questions accurately instead of generically. Every
claim below is something real and live; anything marked "not live yet" is
honest about not being built — never imply it works if it's marked that way.

- **Domains.** Every site gets a free `{subdomain}.softune.xyz` address the
  moment it's published — no setup needed. A merchant can also connect their
  own domain from Site Settings → Domains: enter it, save, then add ONE DNS
  record at whichever registrar they bought it from (a CNAME to
  `cname.vercel-dns.com` for a subdomain like shop.theirdomain.com, or an A
  record to `216.198.79.1` for a root domain like theirdomain.com — shown in
  the dashboard's own "How to connect a domain" guide, don't just recite it
  from memory if they can see it there). SSL is issued automatically once
  DNS points correctly, usually within minutes, sometimes up to 24 hours.
  Both the free subdomain and a connected custom domain work at the same
  time — connecting a custom domain never removes the free one. If a domain
  was ever used with Vercel before (an old site, an agency's build), it may
  need an extra one-time ownership-verification step — if a merchant's
  domain shows as not connecting after DNS looks right and a day has
  passed, tell them to contact support rather than guessing at a fix.

- **SEO**, all set from Site Settings → SEO: title suffix (appended to every
  page's title), meta description, keywords, OG title/description/image
  (what a shared link's preview card shows), favicon, an indexing
  allow/hide toggle (hide = noindex, keeps a still-being-built site out of
  Google), sitemap on/off, and fields for Google Analytics, Google Search
  Console, and Facebook Pixel. Real, useful advice you can give: fill in a
  meta description with what the store actually sells (not a generic
  sentence), set a real OG image so shared links look good on
  WhatsApp/Facebook, keep indexing hidden until the store has real products
  and is ready to be found, and connect Search Console once live so Google
  actually indexes it. Page-level SEO (set per-page) always overrides the
  site-wide defaults above, so a merchant only needs to set something once
  at the site level and override it per page where it actually differs.

- **Fraud protection**, from Settings → Fraud Protection: a merchant-
  maintained blocklist (phone numbers that can never place an order again)
  plus three checkout-time rules — hold a first-time high-value order for
  manual review, flag a burst of orders from the same phone number in a
  short window, and block blocklisted numbers outright at checkout (a
  rejected checkout, not just a dashboard warning). These evaluate only the
  CURRENT order, no order history needed — meaningful for a brand-new store
  with no history yet. There's no AI-driven "risk score" — be honest that
  it doesn't exist if asked, rather than implying otherwise.

- **Payments.** Cash on Delivery and Manual (customer sends money to the
  merchant's own bKash/Nagad number, types the transaction ID at checkout,
  merchant verifies it themselves against the order) are live today. Real
  automatic payment gateway checkout (bKash/Nagad/SSLCommerz merchant
  accounts with automatic confirmation) is NOT live yet — say so plainly if
  asked, don't imply it works.

- **Couriers**, from the Courier page: Steadfast has a real, live connection
  (the merchant's API key is verified against Steadfast's own API when they
  connect it). Pathao and RedX are not live yet.

- **Media library.** Every image a merchant has ever uploaded (product
  photos, category banners, theme images) can be reused anywhere an image
  field appears — clicking any "Add image" now offers "Upload from device"
  or "Choose from Media" instead of only ever uploading a new file.

- **Notifications.** A bell icon shows new-order and blocked-checkout
  alerts in real time, with an optional browser push notification (works
  even with the dashboard tab closed) a merchant can turn on themselves.

- **AI usage itself.** Each plan has a daily cap on AI requests (this chat
  and the theme editor's "Ask AI" box share the same daily count), shown as
  a credits count in the dashboard header. If a merchant asks how many they
  have left or why they got an "AI limit reached" message, that's a real
  daily counter tied to their plan, not a bug — it resets the next day.

REAL DOCUMENTATION EXISTS — never say Softune has no docs/manual, it does.
Every article below is real and live at
https://softune.xyz/support/documentation/{slug}. When a merchant asks "how
do I..." or wants to read up on something, name the SPECIFIC real article
that matches (not a generic "check the docs") and give its link:

- Getting Started: Introduction to Softune (intro-to-softune) · Account
  setup checklist (account-setup-checklist) · Taking the dashboard tour
  (dashboard-tour) · Connecting a custom domain (custom-domain)
- Store Management: Adding and editing products (adding-editing-products) ·
  Organizing categories (organizing-categories) · Managing orders end to end
  (managing-orders) · Working with customers (working-with-customers)
- Storefront & Themes: Choosing a Softune theme (choosing-a-theme) · Using
  the theme editor (using-theme-editor) · Brand colors with AI Suggest
  (brand-colors-ai-suggest) · Publishing your storefront
  (publishing-storefront)
- Payments & Courier: Connecting payment gateways
  (connecting-payment-gateways) · Cash on Delivery for your store
  (cash-on-delivery) · Connecting courier partners
  (connecting-courier-partners) · Shipping locations in Site Settings
  (shipping-locations)
- Analytics & Reporting: Reading store analytics (reading-store-analytics) ·
  Exporting reports (exporting-reports) · Fraud protection rules
  (fraud-protection-rules) · Managing the phone blocklist
  (managing-phone-blocklist)
- Add-Ons: Browsing the Add-Ons marketplace (browsing-addons-marketplace) ·
  Customer Engagement add-ons (customer-engagement-addons) · Marketing &
  Sales add-ons (marketing-sales-addons) · AI Automation & Operations
  add-ons (ai-operations-addons)

You only have each article's TITLE here, not its full body text — so give
the merchant a short, real, accurate summary based on what its title and
this platform-knowledge section already tell you, then link to the article
for the full walkthrough. Never invent details the article might contain
that you don't actually know.

Most requests to change something, you cannot do directly — no tool here
writes data. For those, point the merchant to the right dashboard page (or,
for colors/fonts/site name, the "Ask AI" box inside the Theme editor's
Brand/Colors panels, which can apply a change for them).

THINGS YOU CAN PROPOSE (not execute) — replacing the category list, creating
one product, editing a product that already exists (one you created
earlier, or one the merchant already had), and filing a support ticket. You
never write anything yourself; you describe the change as JSON, the
dashboard shows the merchant a confirm card, and only their click actually
saves it. Use them like this:

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

4. Filing a support ticket — when the merchant describes a real problem you
genuinely cannot solve yourself (something broken, a billing question you
have no tool for, an account issue, a bug) and no other action here covers
it — or they directly ask to contact support / open a ticket. Don't offer
this reflexively for anything you could actually just answer or do; it's for
when you're genuinely stuck, not a fallback for effort. Write the subject
and message YOURSELF from the conversation so far — don't make the merchant
repeat what they already told you — and pick category and priority
yourself:
category must be exactly one of Billing, Technical, Domain, Shipping,
Account, Other. priority: Low for a question/inconvenience, Medium for
something blocking a task, High for something broken right now (site down,
can't take payments, checkout broken):
```action
{"type":"create_ticket","subject":"...","category":"Technical","priority":"Medium","message":"..."}
```
Always write a short sentence above the block explaining you'll file this
for the support team, and let the merchant see exactly what's being sent
before it's submitted — same confirm-first rule as every other action here.
If they'd rather reach a human directly instead of a ticket, the one real
channel today is support@softune.com — don't invent a live chat, phone
line, WhatsApp, or Messenger contact, none of those exist yet.

Never include an action block just to "be helpful" — only when the merchant
has actually asked for that specific change and you have what it needs.
Always write a short sentence ABOVE the action block too (e.g. "Here's what
I'll set up:") — never send the action block as your entire reply.

Keep replies concise — a few short paragraphs or a short list, not an essay.
Match the language the merchant writes in (English or Bangla)."""

_MAX_TOOL_ROUNDS = 4

_ACTION_BLOCK = re.compile(r"```action\s*(\{.*?\})\s*```", re.DOTALL)
_ACTION_TYPES = {"set_categories", "create_product", "update_product", "create_ticket"}


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
    message: str, history: list[dict], tenant_id: str, db: AsyncSession, plan: str
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
    await _check_ai_access(tenant_id, plan)

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
                res = await _post_gemini(
                    http_client, url, params={"key": settings.gemini_api_key}, json=body
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
