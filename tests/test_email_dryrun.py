"""End-to-end dry-run tests for the email delivery layer.

Verifies:
  - HTML file is created in data/archive/
  - No SMTP connection is ever attempted (smtplib is fully mocked)
  - Content structure looks correct

All Claude calls are already mocked by conftest.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dailybrief.delivery.email import send_email
from dailybrief.models import Brief, Item
from dailybrief.render.email_html import render_html
from dailybrief.render.markdown import render_brief

IST = timezone(timedelta(hours=5, minutes=30))
_BASE_DT = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)


def _make_item(n: int, score: float = 10.0, relevance: int = 7) -> Item:
    return Item(
        id=f"hash{n:03d}",
        title=f"RBI Circular on Regulation {n}",
        url=f"https://rbi.org.in/circular/{n}",
        body=f"This is the body of circular {n} with regulatory content.",
        category="RBI",
        source_id="rbi_rss",
        priority=1,
        published_at=_BASE_DT,
        content_hash=f"hash{n:03d}",
        score=score,
        relevance_score=relevance,
        summary=f"Summary of circular {n}. This affects fintech operations.",
        why_it_matters=f"[dry-run placeholder]" if n <= 3 else "",
    )


def _make_brief(n_items: int = 5) -> Brief:
    items = [_make_item(i, score=float(10 - i), relevance=8 - i) for i in range(n_items)]
    return Brief(
        generated_at=datetime.now(timezone.utc),
        items=items,
        date_range_start=_BASE_DT - timedelta(hours=24),
        date_range_end=_BASE_DT,
        top3=items[:3],
    )


# ---------------------------------------------------------------------------
# dry-run saves HTML file
# ---------------------------------------------------------------------------

def test_dry_run_creates_html_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    brief = _make_brief()
    html = render_html(brief)
    plain = render_brief(brief)

    result = send_email(html, plain, "Test Brief", ["boss@company.com"], dry_run=True)

    assert result is True
    archive_dir = tmp_path / "data" / "archive"
    assert archive_dir.exists()
    html_files = list(archive_dir.glob("email_*.html"))
    assert len(html_files) == 1

    content = html_files[0].read_text(encoding="utf-8")
    assert "Daily Regulatory" in content
    assert "RBI Circular on Regulation 0" in content


def test_dry_run_html_contains_top3_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    brief = _make_brief()
    html = render_html(brief)
    plain = render_brief(brief)

    send_email(html, plain, "Test Brief", ["boss@company.com"], dry_run=True)

    archive_dir = tmp_path / "data" / "archive"
    html_files = list(archive_dir.glob("email_*.html"))
    content = html_files[0].read_text(encoding="utf-8")

    # Top-3 section
    assert "Need to Know" in content
    # "Why it matters" placeholder present for top-3 items
    assert "[dry-run placeholder]" in content
    # Remaining items section
    assert "Other Developments" in content


def test_dry_run_html_contains_footer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    brief = _make_brief()
    html = render_html(brief, cost_inr=0.0)
    plain = render_brief(brief)
    send_email(html, plain, "Test Brief", ["boss@company.com"], dry_run=True)

    archive_dir = tmp_path / "data" / "archive"
    html_files = list(archive_dir.glob("email_*.html"))
    content = html_files[0].read_text(encoding="utf-8")
    assert "API cost" in content
    assert "₹" in content


# ---------------------------------------------------------------------------
# GUARDRAIL 3: no SMTP calls in dry-run
# ---------------------------------------------------------------------------

def test_no_smtp_connection_in_dry_run(tmp_path, monkeypatch):
    """Verify smtplib.SMTP and SMTP_SSL are never instantiated in dry-run mode."""
    monkeypatch.chdir(tmp_path)

    with patch("smtplib.SMTP") as mock_smtp, \
         patch("smtplib.SMTP_SSL") as mock_smtp_ssl:

        brief = _make_brief()
        html = render_html(brief)
        plain = render_brief(brief)
        send_email(html, plain, "Test", ["r@example.com"], dry_run=True)

        mock_smtp.assert_not_called()
        mock_smtp_ssl.assert_not_called()


# ---------------------------------------------------------------------------
# Live-send path: SMTP failure → archive still saved, returns False
# ---------------------------------------------------------------------------

def test_smtp_failure_archives_and_returns_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("no server")):
        brief = _make_brief()
        html = render_html(brief)
        plain = render_brief(brief)
        result = send_email(
            html,
            plain,
            "Test",
            ["r@example.com"],
            dry_run=False,
            smtp_host="bad.host",
            smtp_port=587,
        )

    assert result is False
    archive_dir = tmp_path / "data" / "archive"
    assert any(archive_dir.glob("email_*.html"))


# ---------------------------------------------------------------------------
# render_html: basic structural check
# ---------------------------------------------------------------------------

def test_render_html_structure():
    brief = _make_brief(n_items=2)
    html = render_html(brief, cost_inr=1.23)
    assert "<!DOCTYPE html>" in html
    assert "Daily Regulatory" in html
    assert "₹1.2300" in html


def test_render_html_no_items():
    brief = Brief(
        generated_at=datetime.now(timezone.utc),
        items=[],
        date_range_start=_BASE_DT - timedelta(hours=24),
        date_range_end=_BASE_DT,
        top3=[],
    )
    html = render_html(brief)
    assert "No items in this run" in html
