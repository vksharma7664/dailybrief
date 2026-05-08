"""Scraper for SEBI circulars listing page.

URL: https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0
Structure: HTML table — first <td> = date string, any <a> in the row = circular link.
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

BASE_URL = "https://www.sebi.gov.in"
_DATE_FMTS = ("%b %d, %Y", "%B %d, %Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%Y", "%b %d,%Y")


def _parse_date(text: str) -> datetime:
    text = re.sub(r"\s+", " ", text).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


class SEBIFetcher(HTMLFetcher):
    def _parse_html(
        self, html: str, base_url: str, source_id: str, category: str, priority: int
    ) -> list[Item]:
        tree = HTMLParser(html)
        items: list[Item] = []
        seen: set[str] = set()

        for row in tree.css("table tr"):
            cells = row.css("td")
            if len(cells) < 2:
                continue

            link = row.css_first("a")
            if not link:
                continue
            href = link.attributes.get("href", "").strip()
            # Skip navigation/pagination links
            if not href or "doListing" in href or href == "#":
                continue

            title = (link.text(strip=True) or link.attributes.get("title", "")).strip()
            if not title or len(title) < 5:
                continue

            full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            date_text = cells[0].text(strip=True)
            published_at = _parse_date(date_text)

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
            logger.warning("[%s] No items parsed — check SEBI page structure", source_id)
        return items
