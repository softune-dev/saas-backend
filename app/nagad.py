"""Nagad payment gateway — checkout initialize/complete + verification.

UNVERIFIED AGAINST A LIVE SANDBOX — unlike bkash.py (grant-token tested
against bKash's real sandbox) and sslcommerz.py (well-established, widely
documented REST shape), Nagad requires a real merchant account to get a
private key + Nagad's public key, which nobody has provided yet. This
module is a faithful port of Nagad's own documented protocol (RSA-signed,
RSA-encrypted request bodies) and of a real open-source reference
implementation (github.com/MahmudulHassan5809/nagadpy), but has not
actually been run against Nagad's servers. Test carefully against sandbox
credentials before trusting it with real transactions.

Flow: initialize (signed+encrypted merchantId/orderId/challenge) -> decrypt
the response to get paymentReferenceId+challenge -> complete (signed+
encrypted amount/currency, with that challenge) -> customer is redirected
to the returned callBackUrl -> Nagad redirects back to our callback with
payment_ref_id -> verify_payment confirms the real status server-to-server.
"""

import base64
import json
import random
import string
from datetime import UTC, datetime

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# Confirmed against a real open-source client's example callback URL host.
SANDBOX_BASE_URL = "https://sandbox-ssl.mynagad.com:10061/remote-payment-gateway-1.0/api/dfs"
# NOT independently confirmed — verify against Nagad's own merchant portal
# docs once real production credentials exist, before relying on this.
PRODUCTION_BASE_URL = "https://api.mynagad.com:20030/remote-payment-gateway-1.0/api/dfs"

_TIMEOUT_SECONDS = 10.0


def base_url(sandbox: bool) -> str:
    return SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL


def _timestamp() -> str:
    # Nagad expects Asia/Dhaka local time, not UTC — see get_timestamp() in
    # the reference implementation this was ported from.
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Dhaka")).strftime("%Y%m%d%H%M%S")


def _challenge(length: int = 40) -> str:
    return "".join(random.choice(string.ascii_lowercase) for _ in range(length))


def _sign(data: str, merchant_private_key: str) -> str:
    pk = f"-----BEGIN RSA PRIVATE KEY-----\n{merchant_private_key}\n-----END RSA PRIVATE KEY-----"
    private_key = serialization.load_pem_private_key(
        pk.encode(), password=None, backend=default_backend()
    )
    signature = private_key.sign(data.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def _encrypt(data: str, nagad_public_key: str) -> str:
    pk = f"-----BEGIN PUBLIC KEY-----\n{nagad_public_key}\n-----END PUBLIC KEY-----"
    public_key = serialization.load_pem_public_key(pk.encode(), backend=default_backend())
    encrypted = public_key.encrypt(data.encode(), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()


def _decrypt(data_b64: str, merchant_private_key: str) -> str:
    pk = f"-----BEGIN RSA PRIVATE KEY-----\n{merchant_private_key}\n-----END RSA PRIVATE KEY-----"
    private_key = serialization.load_pem_private_key(
        pk.encode(), password=None, backend=default_backend()
    )
    decrypted = private_key.decrypt(base64.b64decode(data_b64), padding.PKCS1v15())
    return decrypted.decode()


async def create_payment(
    merchant_id: str, merchant_private_key: str, nagad_public_key: str, *,
    sandbox: bool, client_ip: str, order_id: str, amount: float, callback_url: str,
) -> tuple[str | None, str | None]:
    """Returns (callback_url_to_redirect_customer_to, error_message).
    Never raises — a malformed key or an unreachable Nagad is an expected,
    user-facing outcome, not a server error (same reasoning throughout this
    codebase's other gateway modules)."""
    url_base = base_url(sandbox)
    headers = {
        "Content-Type": "application/json",
        "X-KM-IP-V4": client_ip,
        "X-KM-Client-Type": "PC_WEB",
        "X-KM-Api-Version": "v-0.2.0",
    }

    try:
        now = _timestamp()
        init_sensitive = json.dumps({
            "merchantId": merchant_id, "orderId": order_id,
            "challenge": _challenge(), "datetime": now,
        })
        init_body = {
            "dateTime": now,
            "sensitiveData": _encrypt(init_sensitive, nagad_public_key),
            "signature": _sign(init_sensitive, merchant_private_key),
        }
        init_url = f"{url_base}/check-out/initialize/{merchant_id}/{order_id}"

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            init_res = await client.post(init_url, json=init_body, headers=headers)
        init_data = init_res.json()

        encrypted_sensitive = init_data.get("sensitiveData")
        if not encrypted_sensitive:
            return None, init_data.get("message") or "Nagad rejected this checkout session."

        decrypted = json.loads(_decrypt(encrypted_sensitive, merchant_private_key))
        payment_reference_id = decrypted.get("paymentReferenceId")
        challenge = decrypted.get("challenge")
        if not payment_reference_id or not challenge:
            return None, "Nagad's response was missing required fields."

        complete_sensitive = json.dumps({
            "merchantId": merchant_id, "orderId": order_id,
            "currencyCode": "050", "amount": f"{amount:.2f}", "challenge": challenge,
        })
        complete_body = {
            "dateTime": _timestamp(),
            "sensitiveData": _encrypt(complete_sensitive, nagad_public_key),
            "signature": _sign(complete_sensitive, merchant_private_key),
            "merchantCallbackURL": callback_url,
            "additionalMerchantInfo": {},
        }
        complete_url = f"{url_base}/check-out/complete/{payment_reference_id}"

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            complete_res = await client.post(complete_url, json=complete_body, headers=headers)
        complete_data = complete_res.json()

        if complete_data.get("status") == "Success" and complete_data.get("callBackUrl"):
            return complete_data["callBackUrl"], None
        return None, complete_data.get("message") or "Nagad rejected this checkout session."

    except httpx.HTTPError as exc:
        return None, f"Couldn't reach Nagad: {exc}"
    except (ValueError, KeyError) as exc:
        return None, f"Nagad returned an unexpected response: {exc}"


async def verify_payment(payment_reference_id: str, *, sandbox: bool) -> tuple[bool, dict | None, str | None]:
    """Returns (ok, data, error). ok=True + data["status"] == "Success" is
    what proves the payment happened — the redirect back to our callback
    URL alone is just a signal to go check, not proof."""
    url = f"{base_url(sandbox)}/verify/payment/{payment_reference_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.get(url)
        data = res.json()
    except httpx.HTTPError as exc:
        return False, None, f"Couldn't reach Nagad: {exc}"
    except ValueError:
        return False, None, "Nagad returned an unexpected response."

    ok = data.get("status") == "Success"
    return ok, data, None if ok else (data.get("message") or "Payment could not be verified.")
