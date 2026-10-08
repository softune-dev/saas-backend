"""Unit tests for the superadmin email campaign pieces that need no database:
audience validation, the plain-text-in / escaped-HTML-out rendering, and the
email address cleaner. Audience resolution and sending are exercised through
the superadmin page against real data."""

import html

import pytest
from pydantic import ValidationError

from app import mailer
from app.api import superadmin_marketing as m


def test_audience_rejects_unknown_plan_and_status():
    with pytest.raises(ValidationError):
        m.AudienceIn(kind="owners", plans=["platinum"])
    with pytest.raises(ValidationError):
        m.AudienceIn(kind="owners", statuses=["vanished"])
    assert m.AudienceIn(kind="owners", plans=["trial"], statuses=["active"]).plans == ["trial"]


def test_button_link_must_be_http():
    with pytest.raises(ValidationError):
        m.MessageIn(subject="s", body="b", cta_label="Go", cta_url="javascript:alert(1)")
    assert m.MessageIn(subject="s", body="b", cta_url="  ").cta_url is None
    assert m.MessageIn(subject="s", body="b", cta_url="https://softunebd.com").cta_url == "https://softunebd.com"


def test_clean_email():
    assert m._clean_email("  Ali@Example.COM ") == "ali@example.com"
    assert m._clean_email("not-an-email") is None
    assert m._clean_email("a b@c.com") is None


def test_rendered_campaign_escapes_operator_text():
    html_body, text_body = mailer.campaign_email(
        headline="Sale <script>",
        paragraphs=["Hello <b>there</b>", "Second"],
        cta_label="Go",
        cta_url='https://x.com/?a=1&b="2"',
        escape=html.escape,
    )
    assert "<script>" not in html_body
    assert "&lt;b&gt;there&lt;/b&gt;" in html_body
    assert 'href="https://x.com/?a=1&amp;b=&quot;2&quot;"' in html_body
    assert "unsubscribe" in html_body.lower()
    assert "Go: https://x.com" in text_body


def test_render_splits_paragraphs_on_blank_lines():
    msg = m.MessageIn(subject="s", body="One\n\nTwo\nstill two\n\nThree")
    html_body, _ = m._render(msg)
    assert html_body.count("font-size:15px;line-height:1.6") == 3
