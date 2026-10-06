"""AI usage log: records every AI call (chat, text generation, theme
suggestion, image generate/edit) into ai_usage_logs, for the superadmin
"AI usage" page. See migrations/072 for the table and what each kind stores.

HOW IT HOOKS IN WITHOUT TOUCHING EVERY CALL SITE: an endpoint wraps its work
in `async with ai_log.track(...) as t:`. That sets a ContextVar, and the two
`_post_gemini` helpers (app/ai.py, app/ai_images.py) call `record_gemini()` on
every response, which adds that call's token counts to whichever tracker is
active. So token totals and call counts come for free, even for a chat turn
that loops through several Gemini round trips for tool use. The endpoint only
fills in what the helpers can't know: the input, the output, and a little
meta.

RULE 9 APPLIES: logging must never fail a request. Writes happen in their own
session (so a rolled-back request still leaves its error row) and every
failure is logged and swallowed. A tracker that is never entered is a no-op.
"""

import asyncio
import base64
import io
import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.db import SessionLocal
from app.models import AiUsageLog

log = logging.getLogger(__name__)

# A chat reply or prompt is a few KB; this only exists so a pathological
# payload can't bloat the table. Longer strings are cut with a marker.
_MAX_TEXT = 8000
_THUMB_MAX_PX = 360


@dataclass
class Tracker:
    kind: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    thumbnail: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    gemini_calls: int = 0
    status: str = "ok"
    error: str | None = None


_current: ContextVar[Tracker | None] = ContextVar("ai_log_tracker", default=None)


def record_gemini(res: httpx.Response, model: str | None = None) -> None:
    """Called by the Gemini helpers on every response. Adds that call's token
    usage to the active tracker, if any. Never raises."""
    tracker = _current.get()
    if tracker is None:
        return
    try:
        tracker.gemini_calls += 1
        if res.status_code != 200:
            return
        data = res.json()
        usage = data.get("usageMetadata") or {}
        tracker.prompt_tokens += int(usage.get("promptTokenCount") or 0)
        tracker.completion_tokens += int(usage.get("candidatesTokenCount") or 0)
        tracker.total_tokens += int(usage.get("totalTokenCount") or 0)
        tracker.model = model or data.get("modelVersion") or tracker.model
    except Exception:  # noqa: BLE001 - logging must not break the call
        log.debug("couldn't read Gemini usage", exc_info=True)


def _clip(value: Any) -> Any:
    """Recursively shorten long strings so one row stays a sane size."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "... [truncated]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def _thumbnail_sync(image_bytes: bytes) -> str | None:
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=70)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


async def make_thumbnail(image_bytes: bytes) -> str | None:
    """A small preview of a generated image for the log. Returns None on any
    problem; a missing preview must never fail the generation."""
    try:
        return await asyncio.to_thread(_thumbnail_sync, image_bytes)
    except Exception:  # noqa: BLE001
        log.warning("couldn't build AI image thumbnail", exc_info=True)
        return None


async def _write(t: Tracker, latency_ms: int) -> None:
    try:
        async with SessionLocal() as session:
            session.add(
                AiUsageLog(
                    tenant_id=t.tenant_id,
                    user_id=t.user_id,
                    kind=t.kind,
                    status=t.status,
                    model=t.model,
                    input=_clip(t.input),
                    output=_clip(t.output),
                    meta=_clip(t.meta),
                    error=(t.error or None) and str(_clip(t.error)),
                    thumbnail=t.thumbnail,
                    prompt_tokens=t.prompt_tokens or None,
                    completion_tokens=t.completion_tokens or None,
                    total_tokens=t.total_tokens or None,
                    gemini_calls=t.gemini_calls,
                    latency_ms=latency_ms,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        log.warning("couldn't write AI usage log", exc_info=True)


@asynccontextmanager
async def track(
    kind: str,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    input: dict[str, Any] | None = None,  # noqa: A002 - mirrors the column
    meta: dict[str, Any] | None = None,
):
    """Wrap one AI request. On a normal exit a status=ok row is written; if
    the body raises (including an HTTPException such as "out of credits"), a
    status=error row with the detail is written and the exception continues."""
    tracker = Tracker(
        kind=kind,
        tenant_id=tenant_id,
        user_id=user_id,
        input=dict(input or {}),
        meta=dict(meta or {}),
    )
    token = _current.set(tracker)
    started = time.monotonic()
    try:
        yield tracker
    except BaseException as exc:
        tracker.status = "error"
        detail = getattr(exc, "detail", None)
        tracker.error = str(detail) if detail else f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _current.reset(token)
        await _write(tracker, int((time.monotonic() - started) * 1000))
