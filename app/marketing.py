"""Meta Conversions API — server-side Purchase events.

Client-side pixels (fbq/ttq/gtag in the storefront templates) miss a real
share of purchases: ad blockers, iOS ITP, and browser tracking prevention all
strip or block them before they ever leave the customer's device. CAPI sends
the same event straight from our server instead, so it survives all of that.

Docs: developers.facebook.com/docs/marketing-api/conversions-api. Requires a
merchant's own Pixel ID (public — see Site.seo.facebook_pixel) and their own
CAPI access token (secret — see app/api/marketing.py's connect flow, stored
Fernet-encrypted via app/courier_crypto.py).

Best-effort by design, same instinct as app/cache.py and app/queue.py (see
CLAUDE.md rule 9): a failed CAPI call must never affect the order that
already succeeded. Errors are returned, not raised; the worker
(app/worker.py's handle_send_meta_capi_event) records them on the connection
so the merchant can see them, and logs them with the token removed.

Event quality: how well Meta can match an event to a person and a product
decides how well ads optimise. So each Purchase carries:
  - customer data, normalised the way Meta requires and then hashed: phone in
    international form (8801712345678, not 01712345678), email, first and last
    name, city, country;
  - the browser's own identifiers (fbp / fbc cookies), when the storefront
    passed them, which is what lets Meta join this server event to the ad click;
  - the products bought (ids, quantities, unit prices) with content_type
    "product", the same ids the pixel sends, so catalog ads can use it.
"""

import hashlib
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

_GRAPH_API_VERSION = "v21.0"
_TIMEOUT_SECONDS = 8.0
_MAX_ERROR_LEN = 300


def _hash(value: str) -> str:
    """Meta requires user_data fields as lowercased, trimmed SHA-256 hashes —
    never send raw PII to their API."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def normalize_phone_e164(raw: str | None) -> str:
    """Digits only, with the country code and no leading 0 or +, which is the
    form Meta hashes (8801712345678). Bangladesh numbers are usually typed
    locally (01712345678); hashing that form never matches Meta's records, so
    the event would carry a phone Meta can't use. A number that already has
    another country's code is left alone."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if digits.startswith("880"):
        # +880 then an accidental extra 0 ("+88001712...") -> 8801712...
        return "880" + digits[3:].lstrip("0")
    if digits.startswith("0"):
        return "880" + digits.lstrip("0")
    if len(digits) == 10 and digits.startswith("1"):
        return "880" + digits
    return digits


def _norm_name(value: str | None) -> str:
    """Meta wants names lowercased with surrounding spaces and punctuation gone."""
    return re.sub(r"[^\wঀ-৿]", "", (value or "").strip().lower())


def scrub(text: str, secret: str | None) -> str:
    """Remove the access token from any text before it is stored or logged, and
    trim it. A network error or Meta's reply can echo the request URL, and the
    token travels in that URL's query string."""
    if secret:
        text = text.replace(secret, "***")
    text = re.sub(r"access_token=[^&\s\"']+", "access_token=***", text)
    return text[:_MAX_ERROR_LEN]


def build_purchase_payload(
    *,
    event_id: str,
    event_time: int,
    value: float,
    currency: str,
    order_number: str,
    contents: list[dict[str, Any]] | None = None,
    customer_phone: str | None = None,
    customer_email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    city: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    fbp: str | None = None,
    fbc: str | None = None,
    event_source_url: str | None = None,
    test_event_code: str | None = None,
) -> dict[str, Any]:
    """The Conversions API request body for one Purchase. Pure (no network), so
    it can be tested exactly."""
    user_data: dict[str, Any] = {"country": [_hash("bd")]}
    phone = normalize_phone_e164(customer_phone)
    if phone:
        user_data["ph"] = [_hash(phone)]
    if customer_email and customer_email.strip():
        user_data["em"] = [_hash(customer_email)]
    if _norm_name(first_name):
        user_data["fn"] = [_hash(_norm_name(first_name))]
    if _norm_name(last_name):
        user_data["ln"] = [_hash(_norm_name(last_name))]
    if _norm_name(city):
        user_data["ct"] = [_hash(_norm_name(city))]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if user_agent:
        user_data["client_user_agent"] = user_agent
    # Not hashed: Meta reads these two as the browser wrote them.
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc

    custom_data: dict[str, Any] = {"currency": currency, "value": value, "order_id": order_number}
    if contents:
        custom_data["content_type"] = "product"
        custom_data["content_ids"] = [c["id"] for c in contents]
        custom_data["contents"] = [
            {"id": c["id"], "quantity": c["quantity"], "item_price": c["item_price"]} for c in contents
        ]
        custom_data["num_items"] = sum(int(c["quantity"]) for c in contents)

    payload: dict[str, Any] = {
        "data": [
            {
                "event_name": "Purchase",
                "event_time": event_time,
                "event_id": event_id,
                "action_source": "website",
                "event_source_url": event_source_url,
                "user_data": user_data,
                "custom_data": custom_data,
            }
        ],
    }
    # While a merchant is testing in Events Manager, events carry their test
    # code so they show up in the test view and stay out of real reporting.
    if test_event_code:
        payload["test_event_code"] = test_event_code
    return payload


async def send_purchase_event(*, pixel_id: str, access_token: str, **event: Any) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Never raises. `event` is the keyword
    arguments of build_purchase_payload.

    `event_id` must match the client-side pixel's fbq('track', 'Purchase', ...,
    {eventID: ...}) call for the same order — that's how Meta deduplicates a
    purchase reported by both the browser pixel and this server call into a
    single conversion instead of double-counting it.

    One retry on a network error or a Meta-side (5xx) failure: those are
    usually momentary, and a lost purchase is expensive. A 4xx is not retried:
    Meta rejected the event itself (bad token, bad pixel id), and sending it
    again would fail the same way.
    """
    payload = build_purchase_payload(**event)
    url = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/{pixel_id}/events"
    last_error = "unknown error"
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                res = await client.post(url, params={"access_token": access_token}, json=payload)
        except httpx.HTTPError as exc:
            last_error = scrub(f"Couldn't reach Meta: {type(exc).__name__}", access_token)
            continue
        if res.status_code == 200:
            return True, None
        last_error = scrub(f"Meta rejected the event ({res.status_code}): {res.text}", access_token)
        if res.status_code < 500:
            break
    return False, last_error
