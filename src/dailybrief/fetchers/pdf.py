"""Full-text enrichment for RSS items with fetch_full_text=True.

Downloads the linked document (PDF or HTML) and replaces item.body with
extracted text (capped at 3 000 chars to keep token costs manageable).
Skips items that already have ≥500 chars of body text.
"""
from __future__ import annotations

import asyncio
import io
import logging
from urllib.parse import urlparse

import httpx

from ..models import Item
from .rss import RateLimiter

logger = logging.getLogger(__name__)

_BODY_THRESHOLD = 500   # chars — skip enrichment if body is already substantial
_MAX_PDF_PAGES = 10
_MAX_BODY_CHARS = 3000


async def _fetch_bytes(url: str, client: httpx.AsyncClient, user_agent: str) -> bytes | None:
    try:
        resp = await client.get(url, timeout=30.0, headers={"User-Agent": user_agent})
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Full-text fetch failed for %s: %s", url, exc)
        return None


def _extract_pdf_text(data: bytes) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages[:_MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            if text:
                parts.append(" ".join(text.split()))
        return " ".join(parts)
    except Exception as exc:
        logger.warning("PDF parse error: %s", exc)
        return ""


def _extract_html_text(data: bytes) -> str:
    try:
        from selectolax.parser import HTMLParser

        html = data.decode("utf-8", errors="replace")
        tree = HTMLParser(html)
        for sel in ("article", "main", ".content", "#content", ".entry-content", ".notification"):
            node = tree.css_first(sel)
            if node:
                text = " ".join(node.text(separator=" ", strip=True).split())
                if len(text) > 100:
                    return text
        body = tree.css_first("body")
        if body:
            return " ".join(body.text(separator=" ", strip=True).split())
        return ""
    except Exception as exc:
        logger.warning("HTML full-text parse error: %s", exc)
        return ""


async def enrich_full_text(
    items: list[Item],
    rate_limiter: RateLimiter | None = None,
    user_agent: str = "DailyBrief/1.0 (internal compliance tool)",
    max_parallel: int = 3,
) -> None:
    """Enrich item.body in-place by following each item's URL.

    Only processes items with body shorter than _BODY_THRESHOLD chars.
    Safe to call with an empty list or when no items need enrichment.
    """
    to_enrich = [item for item in items if len(item.body) < _BODY_THRESHOLD]
    if not to_enrich:
        return

    rl = rate_limiter or RateLimiter()
    sem = asyncio.Semaphore(max_parallel)

    async def _enrich_one(item: Item) -> None:
        domain = urlparse(item.url).netloc
        async with sem:
            await rl.acquire(domain)
            async with httpx.AsyncClient(follow_redirects=True) as client:
                data = await _fetch_bytes(item.url, client, user_agent)
            if data is None:
                return
            if item.url.lower().endswith(".pdf") or data[:4] == b"%PDF":
                text = _extract_pdf_text(data)
            else:
                text = _extract_html_text(data)
            if text:
                item.body = text[:_MAX_BODY_CHARS]

    await asyncio.gather(*[_enrich_one(item) for item in to_enrich], return_exceptions=True)
    logger.info("Full-text enrichment complete for %d items", len(to_enrich))
