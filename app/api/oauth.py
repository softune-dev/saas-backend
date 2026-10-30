"""OAuth endpoints for the ChatGPT plugin (see app/oauth_server.py for the
flow and the security reasoning).

Two routers, because they are called by different parties:
  * `public_router`: ChatGPT and the merchant's browser, no dashboard login —
    discovery metadata, registration, /authorize, /token, /revoke. These must
    NOT sit behind the demo write-guard, which demands a dashboard login.
  * `user_router`: the dashboard, with the merchant logged in — the consent
    page's approve call and the "connected apps" list.
"""

import html
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, oauth_server as oauth
from app.config import settings
from app.db import get_db
from app.models import OAuthToken, Site, Tenant
from app.ratelimit import rate_limit
from app.security import CurrentUser

public_router = APIRouter(tags=["oauth"])
user_router = APIRouter(prefix="/oauth", tags=["oauth"])
DB = Annotated[AsyncSession, Depends(get_db)]

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

SCOPE_TEXT = {
    "store.read": "See your products, orders, categories, sales and store settings",
    "store.write": "Change your store design and homepage text, and add or edit products, categories and sale events",
}


def _error(exc: oauth.OAuthError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.code, "error_description": exc.description},
        status_code=exc.status_code, headers=_NO_STORE,
    )


# --- discovery (RFC 9728 / RFC 8414) --------------------------------------------

@public_router.get("/.well-known/oauth-protected-resource")
@public_router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata() -> dict:
    return {
        "resource": oauth.mcp_resource(),
        "authorization_servers": [oauth.issuer()],
        "scopes_supported": list(oauth.SCOPES),
        "bearer_methods_supported": ["header"],
    }


@public_router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> dict:
    base = oauth.issuer()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(oauth.SCOPES),
        "authorization_response_iss_parameter_supported": True,
    }


# --- registration ------------------------------------------------------------------

@public_router.post(
    "/oauth/register", status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("oauth_register", limit=20, window_seconds=3600))],
)
async def register(request: Request, db: DB):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return _error(oauth.OAuthError("invalid_client_metadata", "Body must be a JSON object."))
    try:
        return JSONResponse(await oauth.register_client(db, body), status_code=201, headers=_NO_STORE)
    except oauth.OAuthError as exc:
        return _error(exc)


# --- authorization (browser) -----------------------------------------------------

def _error_page(message: str) -> HTMLResponse:
    # Shown instead of redirecting: with an unknown client or a redirect_uri
    # we don't recognise, sending the browser anywhere would be an open redirect.
    page = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Can't connect</title><body style='font-family:system-ui;max-width:28rem;margin:4rem auto;padding:0 1rem'>"
        f"<h2>Can't connect</h2><p>{html.escape(message)}</p><p>Go back to ChatGPT and try connecting again.</p>"
    )
    return HTMLResponse(page, status_code=400)


@public_router.get("/oauth/authorize", dependencies=[Depends(rate_limit("oauth_authorize", limit=60, window_seconds=300))])
async def authorize(request: Request, db: DB):
    params = dict(request.query_params)
    client = await oauth.get_client(db, params.get("client_id", ""))
    if client is None or params.get("redirect_uri") not in client.redirect_uris:
        return _error_page("This connection request isn't valid.")
    try:
        req = oauth.validate_authorize(client, params)
    except oauth.OAuthError as exc:
        back = {"state": params.get("state", "")}
        return RedirectResponse(
            oauth.redirect_back({**back, "redirect_uri": params["redirect_uri"]}, error=exc.code, error_description=exc.description),
            status_code=302,
        )
    token = oauth.make_request_token(req)
    return RedirectResponse(f"{settings.dashboard_url.rstrip('/')}/connect/chatgpt?request={token}", status_code=302)


# --- token / revoke --------------------------------------------------------------

@public_router.post("/oauth/token", dependencies=[Depends(rate_limit("oauth_token", limit=120, window_seconds=300))])
async def token(
    db: DB,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    code_verifier: Annotated[str, Form()] = "",
    refresh_token: Annotated[str, Form()] = "",
    resource: Annotated[str | None, Form()] = None,
):
    try:
        if await oauth.get_client(db, client_id) is None:
            raise oauth.OAuthError("invalid_client", "Unknown client.", 401)
        if grant_type == "authorization_code":
            out = await oauth.exchange_code(
                db, client_id=client_id, code=code, redirect_uri=redirect_uri,
                verifier=code_verifier, resource=resource,
            )
        elif grant_type == "refresh_token":
            out = await oauth.refresh_tokens(db, client_id=client_id, refresh_token=refresh_token)
        else:
            raise oauth.OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    except oauth.OAuthError as exc:
        return _error(exc)
    return JSONResponse(out, headers=_NO_STORE)


@public_router.post("/oauth/revoke")
async def revoke(db: DB, token: Annotated[str, Form()]):
    """RFC 7009. Always 200, so it can't be used to probe which tokens exist."""
    h = oauth.hash_secret(token)
    await db.execute(
        update(OAuthToken)
        .where((OAuthToken.access_hash == h) | (OAuthToken.refresh_hash == h), OAuthToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    return JSONResponse({}, headers=_NO_STORE)


# --- consent (the dashboard, merchant logged in) ---------------------------------

class ConsentIn(BaseModel):
    request: str = Field(max_length=4000)
    approve: bool


def _require_owner(user) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner or admin can connect apps to this store.")


@user_router.get("/consent")
async def consent_preview(request: str, user: CurrentUser, db: DB) -> dict:
    """What the consent page shows: who is asking, for which store, to do what."""
    _require_owner(user)
    try:
        req = oauth.read_request_token(request)
    except oauth.OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.description) from exc
    site = (await db.execute(select(Site).where(Site.tenant_id == user.tenant_id).limit(1))).scalars().first()
    return {
        # Always "ChatGPT", never the registered name: anyone can register a
        # client with Dynamic Client Registration and call it "SoftuneBD
        # Support". Every redirect we allow is a chatgpt.com address, so
        # "ChatGPT" is the only honest label.
        "client_name": "ChatGPT",
        "store_name": site.name if site else "your store",
        "permissions": [SCOPE_TEXT[s] for s in req["scope"].split() if s in SCOPE_TEXT],
    }


@user_router.post("/consent")
async def consent(payload: ConsentIn, user: CurrentUser, db: DB) -> dict:
    _require_owner(user)
    try:
        req = oauth.read_request_token(payload.request)
    except oauth.OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.description) from exc
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    if not oauth.tenant_allowed(tenant):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account can't connect apps right now.")
    if not payload.approve:
        return {"redirect_url": oauth.redirect_back(req, error="access_denied", error_description="The merchant declined.")}
    code = await oauth.issue_code(db, tenant_id=user.tenant_id, user_id=user.user_id, req=req)
    return {"redirect_url": oauth.redirect_back(req, code=code)}


# --- connected apps (dashboard) -------------------------------------------------

@user_router.get("/connections")
async def list_connections(user: CurrentUser, db: DB) -> list[dict]:
    now = datetime.now(UTC)
    rows, _ = await crud.list_scoped(
        db, OAuthToken, user.tenant_id,
        filters=[OAuthToken.revoked_at.is_(None), OAuthToken.refresh_expires_at > now],
        order_by=OAuthToken.created_at.desc(), limit=50,
    )
    return [
        {
            "id": str(r.id), "app": "ChatGPT", "scope": r.scope,
            "connected_at": r.created_at, "last_used_at": r.last_used_at,
        }
        for r in rows
    ]


@user_router.delete("/connections/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(token_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    _require_owner(user)
    # get_scoped: another store's id is a 404, never a 403 (CLAUDE.md rule 3).
    row = await crud.get_scoped(db, OAuthToken, user.tenant_id, token_id)
    await oauth._revoke_connection(db, user.tenant_id, row.client_id)
