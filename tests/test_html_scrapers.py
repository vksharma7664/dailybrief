"""Tests for HTML scrapers — SEBI, IRDAI, NPCI.

All tests call _parse_html directly (no real HTTP requests).
The fetch() integration path is covered by a single mock-httpx test per parser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dailybrief.fetchers.html_sebi import SEBIFetcher, _parse_date as sebi_date
from dailybrief.fetchers.html_irdai import IRDAIFetcher, _parse_date as irdai_date
from dailybrief.fetchers.html_npci import NPCIFetcher


# ---------------------------------------------------------------------------
# SEBI parser
# ---------------------------------------------------------------------------

SEBI_HTML = """
<html><body>
<table>
  <thead><tr><th>Date</th><th>Circular</th></tr></thead>
  <tbody>
    <tr>
      <td>Mar 20, 2026</td>
      <td><a href="/legal/circulars/mar-2026/circular-mutual-funds_12345.html">
          Circular on Mutual Funds</a></td>
    </tr>
    <tr>
      <td>Mar 15, 2026</td>
      <td><a href="/legal/circulars/mar-2026/circular-derivatives_12346.html">
          Circular on Derivatives</a></td>
    </tr>
    <tr>
      <!-- navigation row — should be skipped -->
      <td></td>
      <td><a href="HomeAction.do?doListing=yes&amp;sid=1&amp;page=2">Next</a></td>
    </tr>
  </tbody>
</table>
</body></html>
"""


def test_sebi_parses_two_items():
    f = SEBIFetcher()
    items = f._parse_html(SEBI_HTML, "https://www.sebi.gov.in", "sebi_circulars", "Markets", 2)
    assert len(items) == 2


def test_sebi_item_fields():
    f = SEBIFetcher()
    items = f._parse_html(SEBI_HTML, "https://www.sebi.gov.in", "sebi_circulars", "Markets", 2)
    assert items[0].title == "Circular on Mutual Funds"
    assert "sebi.gov.in" in items[0].url
    assert items[0].category == "Markets"
    assert items[0].source_id == "sebi_circulars"


def test_sebi_deduplicates_same_href():
    duplicate_html = """
    <html><body><table>
      <tr><td>Mar 20, 2026</td><td><a href="/circular/same.html">Same Title</a></td></tr>
      <tr><td>Mar 20, 2026</td><td><a href="/circular/same.html">Same Title</a></td></tr>
    </table></body></html>
    """
    f = SEBIFetcher()
    items = f._parse_html(duplicate_html, "", "sebi_circulars", "Markets", 2)
    assert len(items) == 1


def test_sebi_skips_navigation_links():
    f = SEBIFetcher()
    items = f._parse_html(SEBI_HTML, "https://www.sebi.gov.in", "sebi_circulars", "Markets", 2)
    # Only 2 real items, not 3 (the doListing nav link is skipped)
    assert all("doListing" not in item.url for item in items)


def test_sebi_date_parsing():
    assert sebi_date("Mar 20, 2026").month == 3
    assert sebi_date("Mar 20, 2026").day == 20
    assert sebi_date("20-03-2026").month == 3
    assert sebi_date("unparseable").year == datetime.now(timezone.utc).year


def test_sebi_empty_html_returns_empty():
    f = SEBIFetcher()
    items = f._parse_html("<html><body></body></html>", "", "sebi_circulars", "Markets", 2)
    assert items == []


@pytest.mark.asyncio
async def test_sebi_fetch_calls_httpx():
    """Integration: fetch() calls httpx and delegates to _parse_html."""
    f = SEBIFetcher()
    mock_resp = MagicMock()
    mock_resp.text = SEBI_HTML
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("dailybrief.fetchers.html_base.httpx.AsyncClient", return_value=mock_client):
        items = await f.fetch("sebi_circulars", "https://www.sebi.gov.in/...", "Markets", 2)

    assert len(items) == 2


# ---------------------------------------------------------------------------
# IRDAI parser
# ---------------------------------------------------------------------------

IRDAI_HTML = """
<html><body>
<table>
  <tr class="results-row">
    <td><a href="/document-detail?documentId=9196320">
        Circular - Surveyors and Loss Assessors</a></td>
    <td><span>07-04-2026</span></td>
  </tr>
  <tr class="results-row">
    <td><a href="/document-detail?documentId=9196321">
        Motor Insurance Guidelines 2026</a></td>
    <td><span>06-04-2026</span></td>
  </tr>
</table>
</body></html>
"""


def test_irdai_parses_two_items():
    f = IRDAIFetcher()
    items = f._parse_html(IRDAI_HTML, "https://irdai.gov.in", "irdai_circulars", "Insurance", 1)
    assert len(items) == 2


def test_irdai_item_fields():
    f = IRDAIFetcher()
    items = f._parse_html(IRDAI_HTML, "https://irdai.gov.in", "irdai_circulars", "Insurance", 1)
    assert "Surveyors" in items[0].title
    assert "documentId=9196320" in items[0].url
    assert items[0].category == "Insurance"


def test_irdai_date_parsed_from_span():
    f = IRDAIFetcher()
    items = f._parse_html(IRDAI_HTML, "https://irdai.gov.in", "irdai_circulars", "Insurance", 1)
    assert items[0].published_at.month == 4
    assert items[0].published_at.day == 7
    assert items[0].published_at.year == 2026


def test_irdai_date_parsing():
    assert irdai_date("07-04-2026").day == 7
    assert irdai_date("07-04-2026").month == 4
    assert irdai_date("no date here").year == datetime.now(timezone.utc).year


def test_irdai_deduplicates_same_href():
    dup_html = """
    <html><body><table>
      <tr class="results-row">
        <td><a href="/document-detail?documentId=1">Same Circular</a></td>
        <td><span>07-04-2026</span></td>
      </tr>
      <tr class="results-row">
        <td><a href="/document-detail?documentId=1">Same Circular</a></td>
        <td><span>07-04-2026</span></td>
      </tr>
    </table></body></html>
    """
    f = IRDAIFetcher()
    items = f._parse_html(dup_html, "", "irdai_circulars", "Insurance", 1)
    assert len(items) == 1


def test_irdai_fallback_to_all_rows_when_no_class():
    """If no results-row class exists, fall back to all <tr>."""
    plain_html = """
    <html><body>
    <table>
      <tr><td><a href="https://irdai.gov.in/document-detail?documentId=100">Title A</a></td>
          <td><span>01-05-2026</span></td></tr>
    </table>
    </body></html>
    """
    f = IRDAIFetcher()
    items = f._parse_html(plain_html, "https://irdai.gov.in", "irdai_circulars", "Insurance", 1)
    assert len(items) == 1
    assert items[0].title == "Title A"


# ---------------------------------------------------------------------------
# NPCI parser
# ---------------------------------------------------------------------------

NPCI_STATIC_HTML = """
<html><body>
  <div class="press-releases">
    <article>
      <h3><a href="/news/npci-launches-upi-lite-x">NPCI Launches UPI Lite X</a></h3>
      <span class="date">15 Apr 2026</span>
    </article>
    <article>
      <h3><a href="/news/upi-volume-record">UPI Transaction Volume Hits Record</a></h3>
      <span class="date">10 Apr 2026</span>
    </article>
  </div>
</body></html>
"""

NPCI_EMPTY_SPA_HTML = """
<html><body><div id="root"></div></body></html>
"""


def test_npci_parses_static_articles():
    f = NPCIFetcher()
    items = f._parse_html(NPCI_STATIC_HTML, "https://www.npci.org.in", "npci_press", "UPI", 1)
    assert len(items) == 2
    assert items[0].title == "NPCI Launches UPI Lite X"
    assert "npci.org.in" in items[0].url


def test_npci_date_parsed():
    f = NPCIFetcher()
    items = f._parse_html(NPCI_STATIC_HTML, "https://www.npci.org.in", "npci_press", "UPI", 1)
    assert items[0].published_at.month == 4
    assert items[0].published_at.day == 15


def test_npci_spa_returns_empty_list():
    """JS SPA with no static content should return [] without crashing."""
    f = NPCIFetcher()
    items = f._parse_html(NPCI_EMPTY_SPA_HTML, "https://www.npci.org.in", "npci_press", "UPI", 1)
    assert items == []
