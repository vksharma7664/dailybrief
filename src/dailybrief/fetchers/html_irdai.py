"""Scraper for IRDAI circulars listing page.

URL: https://irdai.gov.in/circulars
Structure: <tr class="results-row"> rows containing an <a> title link and
           a date in DD-MM-YYYY format somewhere in the row text.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .html_base import HTMLFetcher
from ..models import Item

logger = logging.getLogger(__name__)

BASE_URL = "https://irdai.gov.in"
_DATE_RE = re.compile(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b")


def _parse_date(text: str) -> datetime:
    m = _DATE_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        try:
            return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class IRDAIFetcher(HTMLFetcher):
    def _parse_html(
        self, html: str, base_url: str, source_id: str, category: str, priority: int
    ) -> list[Item]:
        tree = HTMLParser(html)
        items: list[Item] = []
        seen: set[str] = set()

        # Try class-specific rows first, fall back to all table rows
        rows = tree.css("tr.results-row")
        if not rows:
            rows = tree.css("table tr")

        for row in rows:
            # Prefer links to document-detail pages; fall back to any <a>
            link = row.css_first("a[href*='document-detail']") or row.css_first("a[href*='/document']")
            if not link:
                link = row.css_first("a")
            if not link:
                continue

            href = link.attributes.get("href", "").strip()
            if not href or href == "#":
                continue

            title = link.text(strip=True).strip()
            if not title or len(title) < 5:
                continue

            full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            published_at = _parse_date(row.text())

            items.append(
                self._make_item(
                    title=title,
                    url=full_url,
                    body=title,
                    category=category,
                    source_id=source_id,
                    priority=priority,
                    published_at=published_at,
                )
            )

        if not items:
            logger.warning("[%s] No items parsed — check IRDAI page structure", source_id)
        return items
