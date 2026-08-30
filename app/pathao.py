"""Pathao Courier API — credential verification only, for now.

Pathao uses OAuth2 password-grant: client_id + client_secret + username +
password exchanged for an access token via /aladdin/api/v1/issue-token. That
exchange itself IS the credential check — a bad password/secret gets a 401
back with no token, so no separate "validate" call is needed.

Mirrors app/steadfast.py's shape. Does NOT create Pathao orders yet — same
scope boundary as Steadfast/RedX: prove the credentials work, nothing more.
"""

import httpx

DEFAULT_BASE_URL = "https://api-hermes.pathao.com"

_TIMEOUT_SECONDS = 8.0


async def verify_credentials(
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    base_url: str | None = None,
) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Never raises — same reasoning as
    steadfast.verify_credentials.
    """
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/aladdin/api/v1/issue-token"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "username": username,
                    "password": password,
                    "grant_type": "password",
                },
            )
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach Pathao: {exc}"

    if res.status_code == 200:
        try:
            if "access_token" in res.json():
                return True, None
        except ValueError:
            pass
        return False, "Pathao returned an unexpected response (no access token)."
    if res.status_code in (401, 403, 422):
        return False, "Pathao rejected these credentials."
    return False, f"Pathao returned an unexpected response ({res.status_code})."
