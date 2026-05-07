"""Tests for the RSS fetcher (parsing logic + async fetch with mocked HTTP)."""
from __future__ import annotations

import pytest
import feedparser
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from dailybrief.fetchers.rss import RSSFetcher, RateLimiter, _clean_html

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_xml() -> str:
    return (FIXTURES_DIR / "sample_rbi.xml").read_text(encoding="utf-8")


@pytest.fixture
def fetcher() -> RSSFetcher:
    return RSSFetcher(rate_limiter=RateLimiter(), timeout=5.0, retries=1)


# ---------------------------------------------------------------------------
# HTML cleaning helper
# ---------------------------------------------------------------------------

def test_clean_html_strips_tags():
    assert _clean_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_clean_html_unescapes_entities():
    assert _clean_html("Rs &ndash; 100") == "Rs – 100"
    assert _clean_html("&#8377;500") == "₹500"
    assert _clean_html("&amp;amp;") == "&"   # double-encoded: &amp; → & via html.unescape, then selectolax decodes &amp; → &


def test_clean_html_collapses_whitespace():
    assert _clean_html("a   b\n\nc") == "a b c"


def test_clean_html_empty():
    assert _clean_html("") == ""


# ---------------------------------------------------------------------------
# Parsing tests (no network)
# ---------------------------------------------------------------------------

def test_parse_returns_items(fetcher, sample_xml):
    feed = feedparser.parse(sample_xml)
    items = [fetcher._parse_entry(e, "rbi_press", "RBI", 1) for e in feed.entries]
    items = [i for i in items if i is not None]
    assert len(items) == 4


def test_parse_sets_fields(fetcher, sample_xml):
    feed = feedparser.parse(sample_xml)
    item = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    assert item is not None
    assert "PPI" in item.title
    assert item.category == "RBI"
    assert item.source_id == "rbi_press"
    assert item.priority == 1
    assert item.url.startswith("https://")
    assert item.content_hash


def test_parse_body_is_plain_text(fetcher, sample_xml):
    """Body must have no HTML tags after parsing."""
    feed = feedparser.parse(sample_xml)
    for entry in feed.entries:
        item = fetcher._parse_entry(entry, "rbi_press", "RBI", 1)
        assert item is not None
        assert "<" not in item.body, f"HTML tag found in body: {item.body[:100]}"
        assert "&amp;" not in item.body
        assert "&ndash;" not in item.body


def test_parse_pubdate_matches_fixture(fetcher, sample_xml):
    """BUG 2: published_at must reflect the RSS <pubDate>, not run time."""
    feed = feedparser.parse(sample_xml)
    # First entry: <pubDate>Tue, 06 May 2025 05:30:00 +0000</pubDate>
    item = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    assert item is not None
    assert item.published_at.year == 2025
    assert item.published_at.month == 5
    assert item.published_at.day == 6
    assert item.published_at.hour == 5
    assert item.published_at.minute == 30
    assert item.published_at.tzinfo is not None  # must be timezone-aware


def test_parse_pubdate_second_entry(fetcher, sample_xml):
    """Second entry: <pubDate>Tue, 06 May 2025 03:00:00 +0000</pubDate>"""
    feed = feedparser.parse(sample_xml)
    item = fetcher._parse_entry(feed.entries[1], "rbi_press", "RBI", 1)
    assert item is not None
    assert item.published_at.hour == 3
    assert item.published_at.minute == 0


def test_parse_pubdate_no_timezone_treated_as_ist(fetcher):
    """BUG 2 extra: RBI feeds omit timezone — must be treated as IST, not run time."""
    notz_xml = (FIXTURES_DIR / "sample_rbi_notz.xml").read_text(encoding="utf-8")
    feed = feedparser.parse(notz_xml)
    # Confirm feedparser leaves it as None (the trigger condition)
    assert feed.entries[0].get("published_parsed") is None

    item = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    assert item is not None
    # "19:05:00" without tz → treated as IST → stored as UTC 13:35:00
    assert item.published_at.year == 2026
    assert item.published_at.month == 5
    assert item.published_at.day == 6
    # 19:05 IST = 13:35 UTC
    assert item.published_at.hour == 13
    assert item.published_at.minute == 35


def test_content_hash_is_deterministic(fetcher, sample_xml):
    feed = feedparser.parse(sample_xml)
    item_a = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    item_b = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    assert item_a.content_hash == item_b.content_hash


def test_content_hash_differs_for_different_items(fetcher, sample_xml):
    feed = feedparser.parse(sample_xml)
    item_a = fetcher._parse_entry(feed.entries[0], "rbi_press", "RBI", 1)
    item_b = fetcher._parse_entry(feed.entries[1], "rbi_press", "RBI", 1)
    assert item_a.content_hash != item_b.content_hash


# ---------------------------------------------------------------------------
# Async fetch tests (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_success(fetcher, sample_xml):
    # feedparser receives raw bytes — mock .content (not .text)
    mock_response = MagicMock()
    mock_response.content = sample_xml.encode("utf-8")
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        items = await fetcher.fetch("rbi_press", "https://www.rbi.org.in/rss.xml", "RBI", 1)

    assert len(items) == 4
    assert all(i.category == "RBI" for i in items)
    assert all(i.content_hash for i in items)
    # All items must have proper dates, not run-time fallback
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    assert all(i.published_at.year < now.year or i.published_at < now for i in items)


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_http_error(fetcher):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        items = await fetcher.fetch("rbi_press", "https://www.rbi.org.in/rss.xml")

    assert items == []
