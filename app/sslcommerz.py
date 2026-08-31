"""SSLCommerz payment gateway — checkout session + transaction validation.

Two calls: create_session() gets a GatewayPageURL to redirect the customer
to, and validate_transaction() confirms a completed payment is genuine
before trusting it. SSLCommerz's IPN POST to our webhook is NOT trusted on
its own — it's just a signal to go check; this server-to-server validation
call is the actual proof, and is what stops a forged IPN POST from marking
someone's order paid for free.
"""

import httpx

SANDBOX_BASE_URL = "https://sandbox.sslcommerz.com"
PRODUCTION_BASE_URL = "https://securepay.sslcommerz.com"

_TIMEOUT_SECONDS = 10.0


def base_url(sandbox: bool) -> str:
    return SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL


async def create_session(
    store_id: str, store_passwd: str, *, sandbox: bool,
    tran_id: str, amount: float, currency: str,
    success_url: str, fail_url: str, cancel_url: str, ipn_url: str,
    customer_name: str, customer_email: str, customer_phone: str,
    customer_address: str, product_name: str,
) -> tuple[str | None, str | None]:
    """Returns (gateway_page_url, error_message). Never raises."""
    url = f"{base_url(sandbox)}/gwprocess/v4/api.php"
    data = {
        "store_id": store_id,
        "store_passwd": store_passwd,
        "total_amount": f"{amount:.2f}",
        "currency": currency,
        "tran_id": tran_id,
        "success_url": success_url,
        "fail_url": fail_url,
        "cancel_url": cancel_url,
        "ipn_url": ipn_url,
        "cus_name": customer_name or "Customer",
        "cus_email": customer_email or "customer@example.com",
        "cus_add1": customer_address or "N/A",
        "cus_phone": customer_phone or "N/A",
        "cus_city": "Dhaka",
        "cus_country": "Bangladesh",
        "shipping_method": "NO",
        "product_name": product_name,
        "product_category": "General",
        "product_profile": "general",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(url, data=data)
    except httpx.HTTPError as exc:
        return None, f"Couldn't reach SSLCommerz: {exc}"

    try:
        body = res.json()
    except ValueError:
        return None, "SSLCommerz returned an unexpected response."

    if body.get("status") == "SUCCESS" and body.get("GatewayPageURL"):
        return body["GatewayPageURL"], None
    return None, body.get("failedreason") or "SSLCommerz rejected this checkout session."


async def validate_transaction(
    store_id: str, store_passwd: str, *, sandbox: bool, val_id: str
) -> tuple[bool, dict | None, str | None]:
    """Returns (ok, data, error). ok=True means SSLCommerz's own records
    show this val_id as VALID/VALIDATED — the thing that actually proves
    payment happened, not the IPN call that pointed us here."""
    url = f"{base_url(sandbox)}/validator/api/validationserverAPI.php"
    params = {
        "val_id": val_id, "store_id": store_id, "store_passwd": store_passwd,
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        return False, None, f"Couldn't reach SSLCommerz: {exc}"

    try:
        data = res.json()
    except ValueError:
        return False, None, "SSLCommerz returned an unexpected response."

    ok = data.get("status") in ("VALID", "VALIDATED")
    return ok, data, None if ok else "Transaction could not be validated."
