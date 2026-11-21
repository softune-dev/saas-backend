"""PUBLIC storefront assistant endpoints.

Anonymous, like the rest of /public: a storefront visitor has no account. Two
gates stand in for auth: the site must be published (a draft store's assistant
should not exist to the outside world), and the ask endpoint is rate limited
per IP, because it runs a search on every call.

Nothing a visitor types is stored or forwarded. The assistant answers from the
store's own data; when it cannot, it says so and points to the merchant's own
contact channels (see app/assistant/render.py fallback).
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import _find_published_site
from app.assistant import engine, render, service
from app.assistant.schemas import AskIn, settings_from_row
from app.db import get_db
from app.ratelimit import rate_limit

router = APIRouter(prefix="/public", tags=["public"])
DB = Annotated[AsyncSession, Depends(get_db)]

# The storefront widget is ONE script for every store and every theme, served
# from here so a fix reaches all of them without rebuilding any theme. Read
# once at import: it only changes with a deploy.
_WIDGET_JS = (Path(__file__).resolve().parent.parent / "assistant" / "widget.js").read_text(encoding="utf-8")


@router.get("/assistant/widget.js", include_in_schema=False)
async def widget_script() -> Response:
    """The chat bubble itself. Public and cacheable: it holds no store data,
    only code; everything store-specific comes from /site/{host}/assistant."""
    return Response(
        _WIDGET_JS,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600, stale-while-revalidate=86400"},
    )


@router.get("/site/{host}/assistant")
async def get_assistant_config(host: str, db: DB) -> dict[str, Any]:
    """What the storefront widget needs to draw itself. `enabled: false` is a
    normal answer, not an error: the widget just doesn't render."""
    site = await _find_published_site(host, db)
    cfg = settings_from_row(site.assistant)
    if not cfg.enabled:
        return {"enabled": False}
    index = await service.load_index(db, site, host, cfg)
    return {
        "enabled": True,
        "name": cfg.name,
        "avatar": cfg.avatar,
        "greeting": cfg.greeting,
        "launcher_text": cfg.launcher_text,
        "position": cfg.position,
        "accent": cfg.accent,
        "starters": service.starters(index),
        "channels": render.contact_channels(index),
        # False for a brand-new store: the widget can say it is still learning.
        "ready": bool(index["ready"]),
    }


@router.post(
    "/site/{host}/assistant/ask",
    dependencies=[Depends(rate_limit("assistant", limit=30, window_seconds=300))],
)
async def ask_assistant(host: str, payload: AskIn, db: DB) -> dict[str, Any]:
    site = await _find_published_site(host, db)
    cfg = settings_from_row(site.assistant)
    if not cfg.enabled:
        # 404, like an unpublished site: an off assistant simply isn't there.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant not available")
    index = await service.load_index(db, site, host, cfg)
    reply = await engine.respond(index, payload.question, service.live_loader(db, site), store_name=site.name)
    reply["assistant"] = cfg.name
    if reply["kind"] == "fallback":
        # The merchant's to-do list: questions this store should write an
        # answer for. Never allowed to fail or slow the shopper's reply much.
        await service.record_unanswered(db, site, payload.question, reply["lang"])
    return reply
