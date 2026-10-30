"""POST /mcp: the endpoint ChatGPT calls (see app/mcp_server.py).

Not under the dashboard's login or demo guard: this has its own auth, an OAuth
access token that app/oauth_server.py issued to ChatGPT for one store.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import mcp_server, oauth_server
from app.config import settings
from app.db import get_db

router = APIRouter(tags=["mcp"])
DB = Annotated[AsyncSession, Depends(get_db)]

# ChatGPT's servers send no Origin header. A browser page that does send one
# can only be a different site, so anything but our own and OpenAI's is
# refused (the MCP spec's guard against DNS-rebinding attacks).
_ORIGINS = {"https://chatgpt.com", "https://chat.openai.com"}


def _unauthorized() -> JSONResponse:
    challenge = (
        f'Bearer resource_metadata="{oauth_server.issuer()}/.well-known/oauth-protected-resource", '
        f'scope="{" ".join(oauth_server.SCOPES)}"'
    )
    return JSONResponse(
        {"error": "invalid_token", "error_description": "Missing, expired or invalid access token."},
        status_code=401, headers={"WWW-Authenticate": challenge},
    )


@router.post("/mcp")
async def mcp(request: Request, db: DB):
    origin = request.headers.get("origin")
    if origin and origin not in _ORIGINS and origin not in settings.cors_list:
        return JSONResponse({"error": "forbidden_origin"}, status_code=403)

    auth = request.headers.get("authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    token = await oauth_server.authenticate_bearer(db, raw)
    if token is None:
        return _unauthorized()

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
        )
    if isinstance(body, list):  # older protocol versions allowed batches
        replies = [r for r in [await mcp_server.handle(m, token, db) for m in body] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await mcp_server.handle(body, token, db)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@router.get("/mcp")
async def mcp_get() -> Response:
    # Stateless server: there is no server-to-client event stream to open.
    return Response(status_code=405, headers={"Allow": "POST"})
