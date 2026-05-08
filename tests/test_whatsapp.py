"""Tests for WhatsApp render and delivery."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from dailybrief.models import Brief, Item
from dailybrief.render.whatsapp import MAX_CHARS, render_whatsapp
from dailybrief.delivery.whatsapp import send_whatsapp


def _make_item(n: int, summary: str = "", why: str = "") -> Item:
    item = Item(
        id=f"id{n}",
        title=f"Item Title {n}",
        url=f"https://example.com/{n}",
        body="body",
        category="RBI",
        source_id="rbi_press",
        priority=1,
        published_at=datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc),
        content_hash=f"hash{n}",
    )
    item.summary = summary or f"Summary of item {n}."
    item.why_it_matters = why
    return item


def _make_brief(n_items: int = 5, n_top3: int = 3) -> Brief:
    items = [_make_item(i) for i in range(n_items)]
    top3 = items[:n_top3]
    for item in top3:
        item.why_it_matters = f"Why item {item.id} matters."
    return Brief(
        generated_at=datetime(2026, 5, 8, 3, 0, tzinfo=timezone.utc),  # 8:30 IST
        items=items,
        date_range_start=datetime(2026, 5, 7, tzinfo=timezone.utc),
        date_range_end=datetime(2026, 5, 8, tzinfo=timezone.utc),
        top3=top3,
    )


# ---------------------------------------------------------------------------
# render_whatsapp
# ---------------------------------------------------------------------------

def test_render_contains_header():
    brief = _make_brief()
    text = render_whatsapp(brief)
    assert "Daily Brief" in text
    assert "08 May 2026" in text


def test_render_contains_top3_titles():
    brief = _make_brief()
    text = render_whatsapp(brief)
    for item in brief.top3:
        assert item.title in text


def test_render_contains_why_it_matters():
    brief = _make_brief()
    text = render_whatsapp(brief)
    assert "Why item id0 matters" in text


def test_render_contains_archive_url():
    brief = _make_brief()
    text = render_whatsapp(brief, archive_url="https://brief.example.com/2026-05-08.html")
    assert "brief.example.com" in text


def test_render_within_4000_chars():
    brief = _make_brief(n_items=20, n_top3=3)
    for item in brief.top3:
        item.summary = "A" * 500
        item.why_it_matters = "B" * 500
    text = render_whatsapp(brief, archive_url="https://example.com/brief")
    assert len(text) <= MAX_CHARS


def test_render_truncates_with_ellipsis():
    brief = _make_brief(n_items=3, n_top3=3)
    for item in brief.top3:
        item.title = "T" * 2000
    text = render_whatsapp(brief)
    assert len(text) <= MAX_CHARS
    assert text.endswith("…")


def test_render_empty_top3():
    brief = _make_brief(n_items=0, n_top3=0)
    text = render_whatsapp(brief)
    assert "Daily Brief" in text
    assert isinstance(text, str)


def test_render_item_count_in_header():
    brief = _make_brief(n_items=7)
    text = render_whatsapp(brief)
    assert "7" in text


# ---------------------------------------------------------------------------
# send_whatsapp
# ---------------------------------------------------------------------------

def test_send_dryrun_returns_true():
    result = send_whatsapp(
        message="test message",
        recipients=["+919876543210"],
        account_sid="AC123",
        auth_token="token",
        from_number="whatsapp:+14155238886",
        dry_run=True,
    )
    assert result is True


def test_send_dryrun_no_twilio_call():
    with patch("dailybrief.delivery.whatsapp._TwilioClient") as mock_client_cls:
        send_whatsapp(
            message="test",
            recipients=["+91111"],
            account_sid="sid",
            auth_token="tok",
            from_number="whatsapp:+1",
            dry_run=True,
        )
        mock_client_cls.assert_not_called()


def test_send_empty_recipients_returns_true():
    result = send_whatsapp(
        message="test",
        recipients=[],
        account_sid="sid",
        auth_token="tok",
        from_number="whatsapp:+1",
        dry_run=False,
    )
    assert result is True


def test_send_missing_credentials_returns_false():
    result = send_whatsapp(
        message="test",
        recipients=["+91111"],
        account_sid="",
        auth_token="",
        from_number="",
        dry_run=False,
    )
    assert result is False


def test_send_calls_twilio_for_each_recipient():
    mock_twilio_client = MagicMock()
    mock_twilio_client.messages.create.return_value = MagicMock(sid="SM123")

    with patch("dailybrief.delivery.whatsapp._TwilioClient", return_value=mock_twilio_client):
        result = send_whatsapp(
            message="Hello",
            recipients=["+919876543210", "+918765432109"],
            account_sid="ACfake",
            auth_token="authfake",
            from_number="whatsapp:+14155238886",
            dry_run=False,
        )

    assert result is True
    assert mock_twilio_client.messages.create.call_count == 2


def test_send_adds_whatsapp_prefix():
    mock_twilio_client = MagicMock()
    mock_twilio_client.messages.create.return_value = MagicMock(sid="SM999")

    with patch("dailybrief.delivery.whatsapp._TwilioClient", return_value=mock_twilio_client):
        send_whatsapp(
            message="test",
            recipients=["+919876543210"],
            account_sid="AC",
            auth_token="tok",
            from_number="whatsapp:+1",
            dry_run=False,
        )

    call_kwargs = mock_twilio_client.messages.create.call_args.kwargs
    assert call_kwargs["to"] == "whatsapp:+919876543210"


def test_send_does_not_double_prefix():
    mock_twilio_client = MagicMock()
    mock_twilio_client.messages.create.return_value = MagicMock(sid="SM999")

    with patch("dailybrief.delivery.whatsapp._TwilioClient", return_value=mock_twilio_client):
        send_whatsapp(
            message="test",
            recipients=["whatsapp:+919876543210"],
            account_sid="AC",
            auth_token="tok",
            from_number="whatsapp:+1",
            dry_run=False,
        )

    call_kwargs = mock_twilio_client.messages.create.call_args.kwargs
    assert call_kwargs["to"] == "whatsapp:+919876543210"


def test_send_partial_failure_returns_false():
    mock_twilio_client = MagicMock()
    mock_twilio_client.messages.create.side_effect = [
        MagicMock(sid="SM1"),
        Exception("rate limited"),
    ]

    with patch("dailybrief.delivery.whatsapp._TwilioClient", return_value=mock_twilio_client):
        result = send_whatsapp(
            message="test",
            recipients=["+91111", "+92222"],
            account_sid="AC",
            auth_token="tok",
            from_number="whatsapp:+1",
            dry_run=False,
        )

    assert result is False
