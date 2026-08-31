"""bKash Tokenized Checkout — credential verification AND real checkout.

Four calls: grant token -> create payment -> (customer pays on bKash's own
page) -> execute payment. Query payment exists for reconciliation but isn't
called anywhere yet.

grant_token() doubles as the credential-verification call used at connect
time (app/api/payments.py) — it returns a fresh access token on success. On
a bad app_key/app_secret/username/password combination it still answers 200,
with a statusCode/statusMessage error pair in the body instead of an HTTP
401/403 (confirmed against their real sandbox).
"""

import json

import httpx

SANDBOX_BASE_URL = "https://tokenized.sandbox.bka.sh/v1.2.0-beta"
PRODUCTION_BASE_URL = "https://tokenized.pay.bka.sh/v1.2.0-beta"

_TIMEOUT_SECONDS = 10.0


def base_url(sandbox: bool) -> str:
    return SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL


def _parse(res: httpx.Response) -> dict | None:
    try:
        # See module docstring — bKash's sandbox has been observed sending
        # an unescaped raw newline inside a JSON string value. strict=False
        # tolerates literal control characters in strings instead of
        # failing to parse a response that's otherwise perfectly readable.
        return json.loads(res.text, strict=False)
    except ValueError:
        return None


async def grant_token(
    app_key: str, app_secret: str, username: str, password: str, sandbox: bool
) -> tuple[str | None, str | None]:
    """Returns (id_token, error_message). Never raises."""
    url = f"{base_url(sandbox)}/tokenized/checkout/token/grant"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "username": username,
                    "password": password,
                },
                json={"app_key": app_key, "app_secret": app_secret},
            )
    except httpx.HTTPError as exc:
        return None, f"Couldn't reach bKash: {exc}"

    if res.status_code != 200:
        return None, f"bKash returned an unexpected response ({res.status_code})."
    data = _parse(res)
    if data is None:
        return None, "bKash returned an unexpected response."
    if data.get("id_token"):
        return data["id_token"], None
    return None, data.get("statusMessage") or "bKash rejected these credentials."


async def verify_credentials(
    app_key: str, app_secret: str, username: str, password: str, sandbox: bool = True
) -> tuple[bool, str | None]:
    """Used at connect time (app/api/payments.py) — grant_token itself IS
    the safe verification call, side-effect free beyond issuing a
    short-lived token bKash expires on its own."""
    token, error = await grant_token(app_key, app_secret, username, password, sandbox)
    return token is not None, error


async def create_payment(
    app_key: str, id_token: str, *, sandbox: bool,
    amount: float, currency: str, merchant_invoice_number: str, callback_url: str,
) -> tuple[str | None, str | None, str | None]:
    """Returns (bkash_url, payment_id, error). payment_id is what
    execute_payment needs — stash it (e.g. in the order's meta) between
    this call and the customer coming back from bKash's page."""
    url = f"{base_url(sandbox)}/tokenized/checkout/create"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": id_token,
                    "X-App-Key": app_key,
                },
                json={
                    "mode": "0011",
                    "payerReference": merchant_invoice_number,
                    "callbackURL": callback_url,
                    "amount": f"{amount:.2f}",
                    "currency": currency,
                    "intent": "sale",
                    "merchantInvoiceNumber": merchant_invoice_number,
                },
            )
    except httpx.HTTPError as exc:
        return None, None, f"Couldn't reach bKash: {exc}"

    data = _parse(res)
    if data is None:
        return None, None, "bKash returned an unexpected response."
    if data.get("bkashURL") and data.get("paymentID"):
        return data["bkashURL"], data["paymentID"], None
    return None, None, data.get("statusMessage") or "bKash rejected this checkout session."


async def execute_payment(
    app_key: str, id_token: str, *, sandbox: bool, payment_id: str
) -> tuple[bool, dict | None, str | None]:
    """Returns (ok, data, error). ok=True + data["transactionStatus"] ==
    "Completed" is what proves the payment actually happened — the redirect
    back to callback_url alone is just a signal to go check, not proof."""
    url = f"{base_url(sandbox)}/tokenized/checkout/execute"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": id_token,
                    "X-App-Key": app_key,
                },
                json={"paymentID": payment_id},
            )
    except httpx.HTTPError as exc:
        return False, None, f"Couldn't reach bKash: {exc}"

    data = _parse(res)
    if data is None:
        return False, None, "bKash returned an unexpected response."
    ok = data.get("transactionStatus") == "Completed"
    return ok, data, None if ok else (data.get("statusMessage") or "Payment was not completed.")
