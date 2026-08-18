"""Steadfast Courier API — credential verification only, for now.

Steadfast's merchant API (docs: portal.packzy.com — Steadfast's system runs
under the "Packzy" name internally) authenticates every request with two
headers, `Api-Key` and `Secret-Key`, no OAuth handshake. There's no dedicated
"validate credentials" endpoint, so this calls their balance endpoint — cheap,
side-effect-free, and it 401s immediately on a bad key/secret pair.

This module deliberately does NOT create shipments yet — that's a separate,
larger piece (mapping an Order to a Steadfast consignment) intentionally left
for a later pass. Scope here is exactly "prove these credentials work" to
back the /courier connect flow.
"""

import httpx

DEFAULT_BASE_URL = "https://portal.packzy.com/api/v1"

# Keep this short — a connect request is interactive (a merchant is sitting
# there waiting), so a slow/hung courier API shouldn't hang the whole request.
_TIMEOUT_SECONDS = 8.0


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
