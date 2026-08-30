"""RedX Courier API — credential verification only, for now.

RedX authenticates with a single bearer access token (no key/secret pair,
unlike Steadfast). There's no dedicated "validate token" endpoint, so this
calls their delivery-area list — cheap, side-effect-free, and it 401s
immediately on a bad/expired token.

Mirrors app/steadfast.py's shape exactly. Does NOT create parcels yet — same
scope boundary as Steadfast: prove the credential works, nothing more.
"""

import httpx

DEFAULT_BASE_URL = "https://openapi.redx.com.bd/v1.0.0-beta"

_TIMEOUT_SECONDS = 8.0


async def verify_credentials(
    access_token: str, base_url: str | None = None
) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Never raises — same reasoning as
    steadfast.verify_credentials.
    """
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/areas"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.get(
                url,
                headers={"API-ACCESS-TOKEN": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach RedX: {exc}"

    if res.status_code == 200:
        return True, None
    if res.status_code in (401, 403):
        return False, "RedX rejected this access token."
    return False, f"RedX returned an unexpected response ({res.status_code})."
