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
