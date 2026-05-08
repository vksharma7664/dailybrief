"""Shared base class for HTML-page scrapers.

Handles HTTP GET with rate-limiting and retry; subclasses implement _parse_html.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..models import Item
from .rss import RateLimiter

logger = logging.getLogger(__name__)


class HTMLFetcher:
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
                    resp = await client.get(
                        url,
                        timeout=self._timeout,
                        headers={"User-Agent": self._user_agent},
                    )
                    resp.raise_for_status()
                items = self._parse_html(resp.text, url, source_id, category, priority)
                logger.debug("[%s] Parsed %d items from %s", source_id, len(items), url)
                return items
            except Exception as exc:
                logger.warning(
                    "[%s] Attempt %d/%d failed: %s", source_id, attempt + 1, self._retries, exc
                )
                if attempt < self._retries - 1:
                    await asyncio.sleep(2**attempt)
        logger.error("[%s] All %d attempts failed for %s", source_id, self._retries, url)
        return []

    def _parse_html(
        self, html: str, base_url: str, source_id: str, category: str, priority: int
    ) -> list[Item]:
        raise NotImplementedError

    @staticmethod
    def _make_item(
        title: str,
        url: str,
        body: str,
        category: str,
        source_id: str,
        priority: int,
        published_at: datetime,
    ) -> Item:
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
