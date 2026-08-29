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
already succeeded. Errors are logged and swallowed by the caller
(app/worker.py's handle_send_meta_capi_event), never raised back up.
"""

import hashlib
import logging

import httpx

log = logging.getLogger(__name__)

_GRAPH_API_VERSION = "v21.0"
_TIMEOUT_SECONDS = 8.0


def _hash(value: str) -> str:
    """Meta requires user_data fields as lowercased, trimmed SHA-256 hashes —
    never send raw PII to their API."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


async def send_purchase_event(
    *,
    pixel_id: str,
    access_token: str,
    event_id: str,
    event_time: int,
    value: float,
    currency: str,
    order_number: str,
    customer_phone: str | None,
    customer_email: str | None,
    client_ip: str | None,
    user_agent: str | None,
    event_source_url: str | None,
) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Never raises.

    `event_id` must match the client-side pixel's fbq('track', 'Purchase', ...,
    {eventID: ...}) call for the same order — that's how Meta deduplicates a
    purchase reported by both the browser pixel and this server call into a
    single conversion instead of double-counting it.
    """
    user_data: dict[str, str | list[str]] = {}
    if customer_phone:
        user_data["ph"] = [_hash(customer_phone)]
    if customer_email:
        user_data["em"] = [_hash(customer_email)]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if user_agent:
        user_data["client_user_agent"] = user_agent

    payload = {
        "data": [
            {
                "event_name": "Purchase",
                "event_time": event_time,
                "event_id": event_id,
                "action_source": "website",
                "event_source_url": event_source_url,
                "user_data": user_data,
                "custom_data": {
                    "currency": currency,
                    "value": value,
                    "order_id": order_number,
                },
            }
        ],
    }
    url = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/{pixel_id}/events"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(url, params={"access_token": access_token}, json=payload)
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach Meta: {exc}"

    if res.status_code == 200:
        return True, None
    return False, f"Meta rejected the event ({res.status_code}): {res.text[:200]}"
