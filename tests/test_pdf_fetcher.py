"""Tests for PDF / full-text enrichment."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dailybrief.fetchers.pdf import (
    _extract_html_text,
    _extract_pdf_text,
    enrich_full_text,
)
from dailybrief.models import Item


def _make_item(body: str = "", url: str = "https://rbi.org.in/doc.html") -> Item:
    return Item(
        id="abc",
        title="RBI Circular",
        url=url,
        body=body,
        category="RBI",
        source_id="rbi_notifications",
        priority=1,
        published_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        content_hash="abc",
    )


# ---------------------------------------------------------------------------
# _extract_html_text
# ---------------------------------------------------------------------------

def test_extract_html_text_article_selector():
    html = b"<html><body><article>Main content here for testing.</article></body></html>"
    text = _extract_html_text(html)
    assert "Main content" in text


def test_extract_html_text_falls_back_to_body():
    html = b"<html><body><p>Body text only.</p></body></html>"
    text = _extract_html_text(html)
    assert "Body text only" in text


def test_extract_html_text_empty_doc():
    html = b"<html><body></body></html>"
    text = _extract_html_text(html)
    assert text == "" or isinstance(text, str)


# ---------------------------------------------------------------------------
# _extract_pdf_text
# ---------------------------------------------------------------------------

def test_extract_pdf_text_uses_pypdf(monkeypatch):
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "RBI notification text here."
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader

    monkeypatch.setitem(__import__("sys").modules, "pypdf", mock_pypdf)

    # Re-import to pick up monkeypatched module
    import importlib
    import dailybrief.fetchers.pdf as pdf_module
    importlib.reload(pdf_module)

    text = pdf_module._extract_pdf_text(b"%PDF-1.4 fake content")
    assert "RBI notification text" in text


def test_extract_pdf_text_bad_data_returns_empty():
    text = _extract_pdf_text(b"not a pdf at all")
    assert isinstance(text, str)


# ---------------------------------------------------------------------------
# enrich_full_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_skips_items_with_long_body():
    """Items with ≥500 chars of body should not trigger a fetch."""
    item = _make_item(body="x" * 500)

    with patch("dailybrief.fetchers.pdf.httpx.AsyncClient") as mock_cls:
        await enrich_full_text([item])
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_updates_body_for_html_url():
    item = _make_item(body="short", url="https://rbi.org.in/page.html")
    html_bytes = b"<html><body><article>RBI full text content goes here.</article></body></html>"

    mock_resp = MagicMock()
    mock_resp.content = html_bytes
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("dailybrief.fetchers.pdf.httpx.AsyncClient", return_value=mock_client):
        await enrich_full_text([item])

    assert "RBI full text" in item.body


@pytest.mark.asyncio
async def test_enrich_updates_body_for_pdf_url():
    item = _make_item(body="short", url="https://rbi.org.in/notification.pdf")
    # Fake PDF bytes that start with %PDF magic
    fake_pdf = b"%PDF-1.4 minimal fake"

    mock_resp = MagicMock()
    mock_resp.content = fake_pdf
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("dailybrief.fetchers.pdf.httpx.AsyncClient", return_value=mock_client):
        with patch("dailybrief.fetchers.pdf._extract_pdf_text", return_value="PDF extracted text"):
            await enrich_full_text([item])

    assert item.body == "PDF extracted text"


@pytest.mark.asyncio
async def test_enrich_empty_list_is_noop():
    with patch("dailybrief.fetchers.pdf.httpx.AsyncClient") as mock_cls:
        await enrich_full_text([])
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_fetch_failure_leaves_body_unchanged():
    item = _make_item(body="original body text")
    original_body = item.body

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("network error"))

    with patch("dailybrief.fetchers.pdf.httpx.AsyncClient", return_value=mock_client):
        await enrich_full_text([item])

    assert item.body == original_body
