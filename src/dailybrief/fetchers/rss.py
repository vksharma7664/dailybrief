"""Async RSS fetcher using httpx + feedparser with per-domain rate limiting."""
from __future__ import annotations

import asyncio
import hashlib
import html as html_module
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate
from urllib.parse import urlparse

IST = timezone(timedelta(hours=5, minutes=30))

import feedparser
import httpx
from selectolax.parser import HTMLParser

from ..models import Item

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML cleaning — applied to every field before storing
# ---------------------------------------------------------------------------

def _clean_html(raw: str) -> str:
    """Decode HTML entities and strip tags, returning plain text."""
    if not raw:
        return ""
    # 1. Unescape entities: &ndash; → –, &#8377; → ₹, &amp; → &, etc.
    text = html_module.unescape(raw)
    # 2. Strip tags with selectolax (handles malformed HTML gracefully)
    try:
        tree = HTMLParser(text)
        text = tree.text(separator=" ", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", "", text)
    # 3. Collapse runs of whitespace
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Rate limiter — enforces ≤N req/sec per domain (CLAUDE.md rule 4)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, rate: float = 1.0) -> None:
        self._min_interval = 1.0 / rate
        self._domain_last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, domain: str) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._domain_last.get(domain, 0.0)
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._domain_last[domain] = time.monotonic()


# ---------------------------------------------------------------------------
# RSS fetcher
# ---------------------------------------------------------------------------

class RSSFetcher:
    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        user_agent: str = "DailyBrief/1.0 (internal compliance tool)",
    ) -> None:
        self._rate_limiter = rate_limiter or RateLimiter()
        self._timeout = timeout
        self._retries = retries
        self._user_agent = user_agent

    async def fetch(
        self,
        source_id: str,
        url: str,
        category: str = "Other",
        priority: int = 2,
    ) -> list[Item]:
        domain = urlparse(url).netloc

        for attempt in range(self._retries):
            try:
                await self._rate_limiter.acquire(domain)

                async with httpx.AsyncClient(follow_redirects=True) as client:
                    response = await client.get(
                        url,
                        timeout=self._timeout,
                        headers={"User-Agent": self._user_agent},
                    )
                    response.raise_for_status()

                # Pass raw bytes so feedparser reads the XML encoding declaration
                # directly. httpx's response.text decodes with the HTTP-header
                # charset (often wrong for RBI: "ISO-8859-1" when it's really UTF-8),
                # corrupting multi-byte chars before feedparser ever sees them.
                feed = feedparser.parse(response.content)
                items: list[Item] = []
                for entry in feed.entries:
                    item = self._parse_entry(entry, source_id, category, priority)
                    if item is not None:
                        items.append(item)
                logger.debug(f"[{source_id}] Parsed {len(items)} entries from {url}")
                return items

            except Exception as exc:
                logger.warning(
                    f"[{source_id}] Attempt {attempt + 1}/{self._retries} failed: {exc}"
                )
                if attempt < self._retries - 1:
                    await asyncio.sleep(2**attempt)

        logger.error(f"[{source_id}] All {self._retries} attempts failed for {url}")
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_entry(
        entry: feedparser.util.FeedParserDict,
        source_id: str,
        category: str,
        priority: int,
    ) -> Item | None:
        try:
            # Clean title and body: unescape entities + strip HTML tags
            title: str = _clean_html(entry.get("title") or "")
            url: str = entry.get("link") or ""
            raw_body: str = entry.get("summary") or entry.get("description") or ""
            body: str = _clean_html(raw_body)

            # BUG 2 FIX:
            # (a) Use .get() not getattr — feedparser's __getattr__ returns ''
            #     for missing keys so the None default in getattr() never fires.
            # (b) RBI feeds omit the timezone in pubDate, so feedparser leaves
            #     published_parsed=None. Fall back to parsing the raw string,
            #     treating no-tz dates as IST (all our sources are Indian).
            published_at = datetime.now(timezone.utc)
            parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if parsed_time:
                try:
                    # feedparser normalises tz-aware dates to UTC in struct_time
                    published_at = datetime(*parsed_time[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
            else:
                # Manual fallback for tz-missing date strings (e.g. RBI feeds)
                raw_date = entry.get("published") or entry.get("updated") or ""
                if raw_date:
                    try:
                        t = parsedate(raw_date)
                        if t:
                            # Assume IST for no-tz dates (all sources are Indian)
                            published_at = datetime(*t[:6], tzinfo=IST).astimezone(
                                timezone.utc
                            )
                    except Exception:
                        pass

            # Hash computed on cleaned text for stability
            content_hash = hashlib.sha256(
                (title.lower().strip() + body[:500]).encode()
            ).hexdigest()

            return Item(
                id=content_hash,
                title=title,
                url=url,
                body=body,
                category=category,
                source_id=source_id,
                priority=priority,
                published_at=published_at,
                content_hash=content_hash,
            )
        except Exception as exc:
            logger.warning(f"[{source_id}] Failed to parse entry: {exc}")
            return None
