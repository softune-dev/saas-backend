"""A small OAuth 2.1 authorization server for the ChatGPT plugin.

Why it exists: ChatGPT only talks to an authenticated MCP server through
OAuth 2.1 + PKCE (custom API keys are not accepted), and a merchant must
approve which store ChatGPT may touch. This API is both the authorization
server (this module + app/api/oauth.py) and the resource server
(app/mcp_server.py).

The flow, end to end:
  1. ChatGPT registers itself once per connection (POST /oauth/register, DCR).
  2. ChatGPT sends the merchant's browser to GET /oauth/authorize. We validate
     the request, then hand the merchant to the dashboard's consent page with
     the request signed inside a short-lived token (nothing stored yet).
  3. The merchant, logged in to the dashboard, approves. The dashboard calls
     POST /oauth/consent; we create a one-time code and return the redirect
     back to ChatGPT.
  4. ChatGPT exchanges the code + its PKCE verifier at POST /oauth/token for a
     short-lived access token and a refresh token.
  5. Each MCP call carries the access token; authenticate_bearer() turns it
     back into "this tenant, approved by this user".

Security choices, and why:
  * Tokens and codes are random secrets and only their SHA-256 hash is stored.
  * PKCE (S256) is mandatory; a stolen authorization code is useless without
    the verifier.
  * A code works once. Replaying one is treated as theft: every token for that
    client and store is revoked.
  * Refresh tokens rotate; reusing an old one revokes the whole connection.
  * Redirect URIs must be ChatGPT's own addresses, exactly (no open redirect).
  * Every token is bound to the MCP address it was issued for (RFC 8707
    `resource`), so it can't be replayed against another service.
"""

import base64
import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OAuthClient, OAuthCode, OAuthToken, Tenant

SCOPES = ("store.read", "store.write")
CODE_TTL = timedelta(minutes=10)
REQUEST_TTL = timedelta(minutes=10)

# ChatGPT's own redirect addresses (OpenAI's plugin auth docs). Exact match
# against these two shapes only. Local addresses are allowed outside
# production so the MCP Inspector can be used while developing.
_CHATGPT_REDIRECT = re.compile(r"^https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}$")
_CHATGPT_STABLE_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
_LOCAL_REDIRECT = re.compile(r"^http://(localhost|127\.0\.0\.1):\d{2,5}(/[\w./-]*)?$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    """An OAuth protocol error, rendered as the standard JSON error body."""

    def __init__(self, code: str, description: str, status_code: int = 400):
        super().__init__(description)
        self.code, self.description, self.status_code = code, description, status_code


def issuer() -> str:
    return settings.api_base_url.rstrip("/")


def mcp_resource() -> str:
    return f"{issuer()}/mcp"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not _VERIFIER.match(verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, challenge or "")


def redirect_allowed(uri: str) -> bool:
    if uri == _CHATGPT_STABLE_REDIRECT or _CHATGPT_REDIRECT.match(uri):
        return True
    return settings.is_dev and bool(_LOCAL_REDIRECT.match(uri))


def clean_scope(raw: str | None) -> str:
    asked = (raw or "").split() or list(SCOPES)
    bad = [s for s in asked if s not in SCOPES]
    if bad:
        raise OAuthError("invalid_scope", f"Unknown scope: {bad[0]}")
    # store.write needs to read what it changes.
    if "store.write" in asked and "store.read" not in asked:
        asked.append("store.read")
    return " ".join(s for s in SCOPES if s in asked)


def tenant_allowed(tenant: Tenant | None) -> bool:
    """Same gate as login (app/api/auth.py): suspended, overdue and expired
    trial accounts get nothing — and the shared demo account never connects."""
    if tenant is None or tenant.status != "active" or tenant.plan == "demo":
        return False
    return not (
        tenant.plan == "trial"
        and tenant.trial_expires_at
        and datetime.now(UTC) > tenant.trial_expires_at
    )


# --- client registration (RFC 7591) ------------------------------------------

async def register_client(db: AsyncSession, body: dict) -> dict:
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > 5:
        raise OAuthError("invalid_redirect_uri", "redirect_uris must be a short list.")
    uris = [str(u) for u in uris]
    if not all(redirect_allowed(u) for u in uris):
        raise OAuthError("invalid_redirect_uri", "That redirect address isn't allowed.")
    client = OAuthClient(
        client_id=new_secret("mcp_client"),
        client_name=str(body.get("client_name") or "ChatGPT")[:80],
        redirect_uris=uris,
    )
    db.add(client)
    await db.commit()
    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


async def get_client(db: AsyncSession, client_id: str) -> OAuthClient | None:
    return (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    ).scalar_one_or_none()


# --- authorization request -> consent ------------------------------------------

def validate_authorize(client: OAuthClient | None, params: dict) -> dict:
    """Checks an /oauth/authorize request and returns what the consent page
    needs. Raises OAuthError; the caller must NOT redirect on a bad
    client/redirect_uri (that would be an open redirect), only show an error."""
    if client is None:
        raise OAuthError("invalid_client", "Unknown client.", 401)
    redirect_uri = params.get("redirect_uri") or ""
    if redirect_uri not in client.redirect_uris:
        raise OAuthError("invalid_request", "redirect_uri doesn't match this client.")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only response_type=code is supported.")
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        raise OAuthError("invalid_request", "PKCE with S256 is required.")
    resource = params.get("resource") or mcp_resource()
    if resource != mcp_resource():
        raise OAuthError("invalid_target", "Unknown resource.")
    return {
        "client_id": client.client_id,
        "client_name": client.client_name or "ChatGPT",
        "redirect_uri": redirect_uri,
        "code_challenge": params["code_challenge"],
        "resource": resource,
        "scope": clean_scope(params.get("scope")),
        "state": params.get("state") or "",
    }


def make_request_token(req: dict) -> str:
    now = datetime.now(UTC)
    # typ="oauth_request": the dashboard's own JWTs check typ is "access", so
    # this signed blob can never be used as a login token.
    return jwt.encode(
        {**req, "typ": "oauth_request", "iat": now, "exp": now + REQUEST_TTL},
        settings.secret_key,
        algorithm="HS256",
    )


def read_request_token(token: str) -> dict:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise OAuthError("invalid_request", "This approval link expired. Start again from ChatGPT.") from None
    if data.get("typ") != "oauth_request":
        raise OAuthError("invalid_request", "Not an approval request.")
    return data


def redirect_back(req: dict, **params: str) -> str:
    query = {k: v for k, v in params.items() if v}
    if req.get("state"):
        query["state"] = req["state"]
    # RFC 9207: say who answered, so a client talking to several servers
    # can't be tricked into sending a code to the wrong one.
    query["iss"] = issuer()
    sep = "&" if "?" in req["redirect_uri"] else "?"
    return f"{req['redirect_uri']}{sep}{urlencode(query)}"


async def issue_code(db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, req: dict) -> str:
    code = new_secret("mcp_code")
    db.add(
        OAuthCode(
            tenant_id=tenant_id, user_id=user_id, client_id=req["client_id"],
            code_hash=hash_secret(code), redirect_uri=req["redirect_uri"],
            code_challenge=req["code_challenge"], resource=req["resource"], scope=req["scope"],
            expires_at=datetime.now(UTC) + CODE_TTL,
        )
    )
    await db.commit()
    return code


# --- token endpoint ------------------------------------------------------------

async def _revoke_connection(db: AsyncSession, tenant_id: uuid.UUID, client_id: str) -> None:
    await db.execute(
        update(OAuthToken)
        .where(OAuthToken.tenant_id == tenant_id, OAuthToken.client_id == client_id, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def _mint(db: AsyncSession, *, tenant_id, user_id, client_id, resource, scope) -> dict:
    now = datetime.now(UTC)
    access, refresh = new_secret("mcp_at"), new_secret("mcp_rt")
    db.add(
        OAuthToken(
            tenant_id=tenant_id, user_id=user_id, client_id=client_id,
            access_hash=hash_secret(access), refresh_hash=hash_secret(refresh),
            resource=resource, scope=scope,
            access_expires_at=now + timedelta(minutes=settings.mcp_access_token_minutes),
            refresh_expires_at=now + timedelta(days=settings.mcp_refresh_token_days),
        )
    )
    await db.commit()
    return {
        "access_token": access, "token_type": "Bearer",
        "expires_in": settings.mcp_access_token_minutes * 60,
        "refresh_token": refresh, "scope": scope,
    }


async def exchange_code(
    db: AsyncSession, *, client_id: str, code: str, redirect_uri: str, verifier: str, resource: str | None
) -> dict:
    h = hash_secret(code or "")
    # One atomic UPDATE: of two simultaneous requests carrying the same code,
    # exactly one gets the row back.
    row = (
        await db.execute(
            update(OAuthCode)
            .where(OAuthCode.code_hash == h, OAuthCode.used_at.is_(None), OAuthCode.expires_at > datetime.now(UTC))
            .values(used_at=datetime.now(UTC))
            .returning(OAuthCode)
        )
    ).scalar_one_or_none()
    await db.commit()
    if row is None:
        known = (await db.execute(select(OAuthCode).where(OAuthCode.code_hash == h))).scalar_one_or_none()
        if known is not None and known.used_at is not None:
            await _revoke_connection(db, known.tenant_id, known.client_id)  # replayed code = theft
        raise OAuthError("invalid_grant", "That code is invalid, expired or already used.")
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "Code was issued to a different client.")
    if resource and resource != row.resource:
        raise OAuthError("invalid_target", "Resource doesn't match the approved one.")
    if not pkce_ok(verifier, row.code_challenge):
        raise OAuthError("invalid_grant", "PKCE verification failed.")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == row.tenant_id))).scalar_one_or_none()
    if not tenant_allowed(tenant):
        raise OAuthError("access_denied", "This account can't use the connection right now.", 403)
    return await _mint(
        db, tenant_id=row.tenant_id, user_id=row.user_id, client_id=row.client_id,
        resource=row.resource, scope=row.scope,
    )


async def refresh_tokens(db: AsyncSession, *, client_id: str, refresh_token: str) -> dict:
    h = hash_secret(refresh_token or "")
    now = datetime.now(UTC)
    row = (
        await db.execute(
            update(OAuthToken)
            .where(OAuthToken.refresh_hash == h, OAuthToken.client_id == client_id,
                   OAuthToken.revoked_at.is_(None), OAuthToken.refresh_expires_at > now)
            .values(revoked_at=now)
            .returning(OAuthToken)
        )
    ).scalar_one_or_none()
    await db.commit()
    if row is None:
        old = (await db.execute(select(OAuthToken).where(OAuthToken.refresh_hash == h))).scalar_one_or_none()
        if old is not None and old.revoked_at is not None:
            await _revoke_connection(db, old.tenant_id, old.client_id)  # reused refresh token
        raise OAuthError("invalid_grant", "That refresh token is invalid or expired.")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == row.tenant_id))).scalar_one_or_none()
    if not tenant_allowed(tenant):
        raise OAuthError("access_denied", "This account can't use the connection right now.", 403)
    return await _mint(
        db, tenant_id=row.tenant_id, user_id=row.user_id, client_id=row.client_id,
        resource=row.resource, scope=row.scope,
    )


# --- resource server side --------------------------------------------------------

async def authenticate_bearer(db: AsyncSession, raw_token: str) -> OAuthToken | None:
    """The token row if `raw_token` is a live access token for THIS MCP
    server and its store may still use it, else None."""
    if not raw_token:
        return None
    now = datetime.now(UTC)
    token = (
        await db.execute(select(OAuthToken).where(OAuthToken.access_hash == hash_secret(raw_token)))
    ).scalar_one_or_none()
    if (
        token is None or token.revoked_at is not None
        or token.access_expires_at <= now or token.resource != mcp_resource()
    ):
        return None
    tenant = (await db.execute(select(Tenant).where(Tenant.id == token.tenant_id))).scalar_one_or_none()
    if not tenant_allowed(tenant):
        return None
    # Recorded at most once a minute: shown in "connected apps", and not
    # worth a database write on every tool call.
    if token.last_used_at is None or (now - token.last_used_at).total_seconds() > 60:
        token.last_used_at = now
        await db.commit()
    return token
