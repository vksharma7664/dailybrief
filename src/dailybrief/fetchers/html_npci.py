"""Scraper for NPCI UPI press releases.

URL: https://www.npci.org.in/what-we-do/upi/press-releases
Note: NPCI's website is a React SPA; this scraper handles server-side or static
      HTML if present. It returns an empty list (with a warning) when JS rendering
      is required — callers should treat this as a non-fatal empty source.
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

BASE_URL = "https://www.npci.org.in"
_DATE_FMTS = ("%d %b %Y", "%B %d, %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y")


def _parse_date(text: str) -> datetime:
    text = re.sub(r"\s+", " ", text).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# CSS selectors tried in priority order to find press-release list items
_ITEM_SELECTORS = [
    "article",
    ".press-release-item",
    ".press-releases-item",
    ".listing-item",
    ".news-item",
    "li.item",
    ".media-coverage-item",
]


class NPCIFetcher(HTMLFetcher):
    def _parse_html(
        self, html: str, base_url: str, source_id: str, category: str, priority: int
    ) -> list[Item]:
        tree = HTMLParser(html)
        items: list[Item] = []
        seen: set[str] = set()

        candidates = []
        for sel in _ITEM_SELECTORS:
            nodes = tree.css(sel)
            if nodes:
                candidates = nodes
                break

        for node in candidates:
            link = node.css_first("a")
            if not link:
                continue
            href = link.attributes.get("href", "").strip()
            if not href or href == "#":
                continue

            title_node = node.css_first("h2, h3, h4, .title, .heading")
            title = (title_node.text(strip=True) if title_node else link.text(strip=True)).strip()
            if not title or len(title) < 5:
                continue

            full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            date_node = node.css_first(".date, time, [class*='date']")
            date_text = date_node.text(strip=True) if date_node else ""
            published_at = _parse_date(date_text) if date_text else datetime.now(timezone.utc)

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
            logger.warning(
                "[%s] No items parsed — NPCI site may require JavaScript rendering", source_id
            )
        return items
