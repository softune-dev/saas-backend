"""Steadfast Courier API — credential verification, consignment booking, and
the delivery-status webhook's status vocabulary.

Steadfast's merchant API (docs: portal.packzy.com — Steadfast's system runs
under the "Packzy" name internally) authenticates every request with two
headers, `Api-Key` and `Secret-Key`, no OAuth handshake. There's no dedicated
"validate credentials" endpoint, so verify_credentials calls their balance
endpoint — cheap, side-effect-free, and it 401s immediately on a bad
key/secret pair.

DELIVERY STATUS COMES BACK TWO WAYS, both landing on Order.delivery_status:
  1. create_consignment's own response ("in_review" immediately on booking).
  2. Steadfast's webhook, called on THEIR schedule as the parcel moves
     (in_review -> delivered/cancelled/... — see app/api/public.py's
     steadfast_webhook). There is no polling here; Steadfast pushes updates.

STATUS_MAP translates Steadfast's own status strings (their wording, not
ours) into the small vocabulary the rest of the app treats as canonical.
Unknown values pass through unchanged rather than raising — a courier
API adding a new status string must never break the webhook receiver.
"""

import httpx

DEFAULT_BASE_URL = "https://portal.packzy.com/api/v1"

# Keep this short — a connect request is interactive (a merchant is sitting
# there waiting), so a slow/hung courier API shouldn't hang the whole request.
_TIMEOUT_SECONDS = 8.0

STATUS_MAP: dict[str, str] = {
    "pending": "in_review",
    "in_review": "in_review",
    "approved": "in_review",
    "delivered": "delivered",
    "partial_delivered": "delivered",
    "cancelled": "cancelled",
    "delivered_approval_pending": "delivered",
    "partial_delivered_approval_pending": "delivered",
    "cancelled_approval_pending": "cancelled",
    "unknown_approval_pending": "in_review",
}


async def verify_credentials(
    api_key: str, secret_key: str, base_url: str | None = None
) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Never raises — a courier outage or a
    typo'd key is an expected, user-facing outcome, not a server error.
    """
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/get_balance"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.get(
                url,
                headers={"Api-Key": api_key, "Secret-Key": secret_key},
            )
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach Steadfast: {exc}"

    if res.status_code == 200:
        return True, None
    if res.status_code in (401, 403):
        return False, "Steadfast rejected these credentials."
    return False, f"Steadfast returned an unexpected response ({res.status_code})."


async def create_consignment(
    *,
    api_key: str,
    secret_key: str,
    invoice: str,
    recipient_name: str,
    recipient_phone: str,
    recipient_address: str,
    cod_amount_cents: int,
    note: str | None = None,
    base_url: str | None = None,
) -> tuple[dict | None, str | None]:
    """Books one shipment. Returns (result, error_message) — result has
    consignment_id/tracking_code/status on success, is None on failure.

    `invoice` is our own order_number (e.g. "ORD-1403"), not a Steadfast id —
    it round-trips back on the webhook payload, so the receiver can find the
    Order even before consignment_id is looked up. cod_amount_cents follows
    CLAUDE.md rule 7 (money as integer cents) like everywhere else in this
    codebase; Steadfast's own API wants a decimal taka amount, so the
    conversion happens here, once, not at every call site.
    """
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/create_order"
    payload = {
        "invoice": invoice,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "recipient_address": recipient_address,
        "cod_amount": cod_amount_cents / 100,
        "note": note or "",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                json=payload,
                headers={"Api-Key": api_key, "Secret-Key": secret_key},
            )
    except httpx.HTTPError as exc:
        return None, f"Couldn't reach Steadfast: {exc}"

    if res.status_code not in (200, 201):
        return None, f"Steadfast returned an unexpected response ({res.status_code})."

    body = res.json()
    consignment = body.get("consignment") or {}
    if not consignment.get("consignment_id"):
        return None, "Steadfast accepted the request but returned no consignment id."

    return {
        "consignment_id": str(consignment["consignment_id"]),
        "tracking_code": consignment.get("tracking_code"),
        "status": STATUS_MAP.get(consignment.get("status", ""), "in_review"),
    }, None
