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

import json

import httpx

DEFAULT_BASE_URL = "https://portal.packzy.com/api/v1"

# Keep this short — a connect request is interactive (a merchant is sitting
# there waiting), so a slow/hung courier API shouldn't hang the whole request.
_TIMEOUT_SECONDS = 8.0

# Full vocabulary per Steadfast's own docs (portal.packzy.com/api-docs):
# pending, delivered_approval_pending, partial_delivered_approval_pending,
# cancelled_approval_pending, unknown_approval_pending, delivered,
# partial_delivered, cancelled, hold, in_review, unknown.
STATUS_MAP: dict[str, str] = {
    "pending": "in_review",
    "in_review": "in_review",
    "approved": "in_review",
    "hold": "in_review",
    "unknown": "in_review",
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


def _order_payload(
    *,
    invoice: str,
    recipient_name: str,
    recipient_phone: str,
    recipient_address: str,
    cod_amount_cents: int,
    note: str | None,
    recipient_email: str | None,
    alternative_phone: str | None,
    item_description: str | None,
    total_lot: int | None,
) -> dict:
    """Shared field-shaping for both /create_order and the bulk endpoint —
    exact field names per Steadfast's docs. delivery_type is intentionally
    not exposed here: leaving it unset defaults to home delivery (0), the
    only mode a checkout-collected address supports; point/hub delivery (1)
    would need a hub picker our checkout doesn't have.
    """
    payload = {
        "invoice": invoice,
        "recipient_name": recipient_name[:100],
        "recipient_phone": recipient_phone,
        "recipient_address": recipient_address[:250],
        "cod_amount": cod_amount_cents / 100,
        "note": note or "",
    }
    if recipient_email:
        payload["recipient_email"] = recipient_email
    if alternative_phone:
        payload["alternative_phone"] = alternative_phone
    if item_description:
        payload["item_description"] = item_description
    if total_lot:
        payload["total_lot"] = total_lot
    return payload


def _consignment_result(consignment: dict) -> dict:
    return {
        "consignment_id": str(consignment["consignment_id"]),
        "tracking_code": consignment.get("tracking_code"),
        "status": STATUS_MAP.get(consignment.get("status", ""), "in_review"),
    }


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
    recipient_email: str | None = None,
    alternative_phone: str | None = None,
    item_description: str | None = None,
    total_lot: int | None = None,
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
    payload = _order_payload(
        invoice=invoice, recipient_name=recipient_name, recipient_phone=recipient_phone,
        recipient_address=recipient_address, cod_amount_cents=cod_amount_cents, note=note,
        recipient_email=recipient_email, alternative_phone=alternative_phone,
        item_description=item_description, total_lot=total_lot,
    )
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

    return _consignment_result(consignment), None


async def create_bulk_consignments(
    *,
    api_key: str,
    secret_key: str,
    orders: list[dict],
    base_url: str | None = None,
) -> tuple[list[dict], str | None]:
    """Books up to 500 shipments in one call. `orders` items use the same
    keyword shape as create_consignment (invoice/recipient_name/...).

    Steadfast's bulk endpoint has an unusual request shape: `data` is not a
    JSON array, it's a JSON-encoded STRING containing one — confirmed
    against their published docs, not a guess. Getting this wrong (sending
    a real array) fails silently with a generic error, so this is
    deliberately the ONLY place that does the double-encoding, rather than
    leaving each call site to remember it.

    Returns (results, error_message). On success, results is one dict per
    input order in the SAME order, each either the create_consignment shape
    (success) or {"error": invoice, "message": str} (that one order was
    rejected — e.g. a duplicate invoice — without failing the whole batch).
    """
    if not orders:
        return [], "No orders to book."
    if len(orders) > 500:
        return [], "Steadfast allows at most 500 orders per bulk request."

    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/create_order/bulk-order"
    payloads = [
        _order_payload(
            invoice=o["invoice"], recipient_name=o["recipient_name"],
            recipient_phone=o["recipient_phone"], recipient_address=o["recipient_address"],
            cod_amount_cents=o["cod_amount_cents"], note=o.get("note"),
            recipient_email=o.get("recipient_email"), alternative_phone=o.get("alternative_phone"),
            item_description=o.get("item_description"), total_lot=o.get("total_lot"),
        )
        for o in orders
    ]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS * 3) as client:
            res = await client.post(
                url,
                json={"data": json.dumps(payloads)},
                headers={"Api-Key": api_key, "Secret-Key": secret_key},
            )
    except httpx.HTTPError as exc:
        return [], f"Couldn't reach Steadfast: {exc}"

    if res.status_code not in (200, 201):
        return [], f"Steadfast returned an unexpected response ({res.status_code})."

    body = res.json()
    rows = body if isinstance(body, list) else body.get("data") or []
    results = []
    for row in rows:
        consignment = row.get("consignment")
        if row.get("status") == "success" and consignment:
            results.append(_consignment_result(consignment))
        else:
            results.append({
                "error": row.get("invoice"),
                "message": row.get("message") or "Steadfast rejected this order.",
            })
    return results, None


async def check_status(
    *,
    api_key: str,
    secret_key: str,
    consignment_id: str,
    base_url: str | None = None,
) -> tuple[str | None, str | None]:
    """Polling fallback / reconciliation check — same canonical status
    vocabulary as the webhook. Not the primary update path (the webhook is),
    but a merchant's Steadfast account might not have a webhook configured,
    or a callback might get dropped, so this is what a periodic sync job
    (or a manual "refresh status" action) calls instead of trusting the
    webhook alone.
    """
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/status_by_cid/{consignment_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.get(
                url,
                headers={"Api-Key": api_key, "Secret-Key": secret_key},
            )
    except httpx.HTTPError as exc:
        return None, f"Couldn't reach Steadfast: {exc}"

    if res.status_code != 200:
        return None, f"Steadfast returned an unexpected response ({res.status_code})."

    raw = res.json().get("delivery_status", "")
    return STATUS_MAP.get(raw, raw), None
