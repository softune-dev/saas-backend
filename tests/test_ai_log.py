"""Unit tests for app/ai_log.py's pure parts: token accumulation from Gemini
responses, text clipping, and the tracker lifecycle. The row write itself goes
through its own DB session and is covered by the superadmin page; here the
write is stubbed so nothing needs a database."""

import uuid

import httpx
import pytest

from app import ai_log


def _gemini_response(status: int = 200, usage: dict | None = None) -> httpx.Response:
    body = {"usageMetadata": usage or {}, "modelVersion": "gemini-test"}
    return httpx.Response(status, json=body)


@pytest.fixture
def written(monkeypatch):
    rows: list[tuple[ai_log.Tracker, int]] = []

    async def fake_write(tracker, latency_ms):
        rows.append((tracker, latency_ms))

    monkeypatch.setattr(ai_log, "_write", fake_write)
    return rows


async def test_tokens_accumulate_across_gemini_calls(written):
    async with ai_log.track("chat", tenant_id=uuid.uuid4(), user_id=None) as t:
        ai_log.record_gemini(
            _gemini_response(usage={"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14})
        )
        ai_log.record_gemini(
            _gemini_response(usage={"promptTokenCount": 20, "candidatesTokenCount": 6, "totalTokenCount": 26})
        )
    assert (t.prompt_tokens, t.completion_tokens, t.total_tokens) == (30, 10, 40)
    assert t.gemini_calls == 2
    assert t.model == "gemini-test"
    assert written[0][0].status == "ok"


async def test_failed_gemini_call_counts_but_adds_no_tokens(written):
    async with ai_log.track("chat", tenant_id=uuid.uuid4(), user_id=None) as t:
        ai_log.record_gemini(_gemini_response(status=503))
    assert t.gemini_calls == 1
    assert t.total_tokens == 0


async def test_exception_is_logged_as_error_and_still_raised(written):
    class Boom(Exception):
        detail = "out of credits"

    with pytest.raises(Boom):
        async with ai_log.track("image_generate", tenant_id=uuid.uuid4(), user_id=None):
            raise Boom()
    tracker, _ = written[0]
    assert tracker.status == "error"
    assert tracker.error == "out of credits"


def test_record_without_a_tracker_is_a_noop():
    ai_log.record_gemini(_gemini_response(usage={"totalTokenCount": 5}))


def test_clip_shortens_long_strings_recursively():
    long = "x" * (ai_log._MAX_TEXT + 50)
    clipped = ai_log._clip({"a": [long], "b": "short"})
    assert clipped["a"][0].endswith("[truncated]")
    assert clipped["b"] == "short"


async def test_chat_endpoint_logs_the_call_for_a_real_logged_in_user(account):
    """Regression: the endpoint passed `user.id`, but a request's principal only
    has `user_id`, which turned every AI call into a 500 in production. Whatever
    the model does (here: no key, so a clean 4xx/5xx from the AI layer), the
    request must get past the logging wrapper and leave an audit row."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AiUsageLog

    res = await account.post("/ai/chat", json={"message": "how many orders do I have?"})
    assert res.status_code != 500 or "Principal" not in res.text
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(AiUsageLog).where(AiUsageLog.tenant_id == account.tenant_id))
        ).scalars().all()
    assert rows and rows[0].kind == "chat" and rows[0].user_id is not None


async def test_audit_record_accepts_a_request_principal(account):
    from sqlalchemy import select

    from app import audit
    from app.db import SessionLocal
    from app.models import AdminAuditLog, User
    from app.security import Principal

    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == account.email))).scalar_one()
    actor = Principal(user_id=user.id, tenant_id=user.tenant_id, role="owner", is_superadmin=True)
    await audit.record(actor, "test.action", "tenant", account.tenant_id, "Test WS")
    async with SessionLocal() as db:
        row = (
            await db.execute(select(AdminAuditLog).where(AdminAuditLog.target_id == account.tenant_id))
        ).scalars().first()
    assert row is not None and row.actor_email == account.email
