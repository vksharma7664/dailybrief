"""Orchestrator for Day 1.

Fetches all RSS sources, deduplicates, filters to last 24 h IST,
scores by keyword, deduplicates near-duplicates, and renders a Markdown brief.

Usage:
    uv run python -m dailybrief.main
    uv run python -m dailybrief.main --dry-run
    uv run python -m dailybrief.main --output brief.md
    uv run python -m dailybrief.main --output brief.md --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

# Force UTF-8 stdout before any output — fixes Windows CP437 mojibake on terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from datetime import datetime, timezone
from pathlib import Path

from .fetchers.rss import RateLimiter, RSSFetcher
from .models import Brief
from .pipeline.dedupe import DedupeStore
from .pipeline.filter import dedup_near_duplicates, filter_items, last_24h_window
from .render.markdown import render_brief
from .settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


async def run(dry_run: bool = False, output_file: str | None = None) -> None:
    settings = Settings()

    rate_limiter = RateLimiter(
        rate=settings.source_defaults.get("rate_limit_per_domain", 1.0)
    )
    fetcher = RSSFetcher(
        rate_limiter=rate_limiter,
        timeout=float(settings.source_defaults.get("timeout_seconds", 30)),
        retries=int(settings.source_defaults.get("retries", 3)),
        user_agent=settings.source_defaults.get(
            "user_agent", "DailyBrief/1.0 (internal compliance tool)"
        ),
    )

    rss_sources = [s for s in settings.sources if s.type == "rss"]
    logger.info(f"Fetching {len(rss_sources)} RSS sources…")

    tasks = [
        fetcher.fetch(s.id, s.url, s.category, s.priority) for s in rss_sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for source, result in zip(rss_sources, results):
        if isinstance(result, Exception):
            logger.error(f"[{source.id}] Fetch error: {result}")
        else:
            logger.info(f"[{source.id}] {len(result)} items fetched")
            all_items.extend(result)

    logger.info(f"Total fetched: {len(all_items)}")

    # SQLite content-hash dedupe
    db_path = Path("data/seen_dryrun.db" if dry_run else "data/seen.db")
    with DedupeStore(db_path) as store:
        new_items = store.filter_new(all_items)
    logger.info(f"After dedupe: {len(new_items)} new items")

    # 24-h IST window + keyword scoring
    filtered = filter_items(
        new_items,
        settings.keywords_high,
        settings.keywords_medium,
        settings.keywords_exclude,
    )
    logger.info(f"After filter: {len(filtered)} items in 24-h window")

    # Within-run near-duplicate removal (Jaccard on title tokens)
    deduped = dedup_near_duplicates(filtered)
    logger.info(
        f"After near-dup: {len(deduped)} items "
        f"(dropped {len(filtered) - len(deduped)} near-duplicates)"
    )

    start, end = last_24h_window()
    brief = Brief(
        generated_at=datetime.now(timezone.utc),
        items=deduped,
        date_range_start=start,
        date_range_end=end,
    )

    rendered = render_brief(brief)

    if output_file:
        # Write with explicit UTF-8 — avoids any terminal encoding issues
        Path(output_file).write_text(rendered, encoding="utf-8")
        logger.info(f"Brief written to {output_file!r} ({len(deduped)} items)")
    else:
        print(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily regulatory & banking brief"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a separate test DB (data/seen_dryrun.db)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write brief to FILE (UTF-8) instead of stdout",
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, output_file=args.output))


if __name__ == "__main__":
    main()
