"""Orchestrator for Day 2.

Pipeline:
  fetch → dedup → filter (24h window + keyword score) →
  [limit N] → preflight estimate →
  summarize (Claude batch, or RSS fallback if DRY_RUN_SKIP_CLAUDE) →
  rank (top 3) →
  generate "why it matters" for top 3 →
  render HTML email →
  deliver (SMTP or dry-run archive)

CLI flags:
  --dry-run / --no-dry-run      override DRY_RUN env var
  --limit N                     cap items processed (cost control)
  --since "06 May 2026 00:00"   override 24-h window start (backfill)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()  # must run before Settings() so env vars reach the anthropic SDK

from .delivery.email import send_email
from .fetchers.rss import RateLimiter, RSSFetcher
from .models import Brief
from .pipeline.cost_tracker import cost_tracker
from .pipeline.dedupe import DedupeStore
from .pipeline.filter import IST, dedup_near_duplicates, filter_items, last_24h_window
from .pipeline.ranker import select_top_3
from .pipeline.summarizer import summarize_batch
from .pipeline.why_it_matters import generate_why
from .render.email_html import render_html
from .render.markdown import render_brief
from .settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

_SINCE_FMT = "%d %b %Y %H:%M"


def _parse_since(s: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(s, _SINCE_FMT).replace(tzinfo=IST)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--since must be like '06 May 2026 00:00', got: {s!r}"
        )
    end = datetime.now(IST)
    return start, end


async def run(
    dry_run: bool,
    limit: int | None,
    since: str | None,
) -> None:
    settings = Settings()
    cost_tracker.reset()

    # --- fetch ---
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
    logger.info("Fetching %d RSS sources…", len(rss_sources))

    tasks = [fetcher.fetch(s.id, s.url, s.category, s.priority) for s in rss_sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for source, result in zip(rss_sources, results):
        if isinstance(result, Exception):
            logger.error("[%s] Fetch error: %s", source.id, result)
        else:
            logger.info("[%s] %d items fetched", source.id, len(result))
            all_items.extend(result)

    logger.info("Total fetched: %d", len(all_items))

    # --- dedupe (SQLite content-hash) ---
    db_path = Path("data/seen_dryrun.db" if dry_run else "data/seen.db")
    with DedupeStore(db_path) as store:
        new_items = store.filter_new(all_items)
    logger.info("After dedupe: %d new items", len(new_items))

    # --- filter ---
    window = _parse_since(since) if since else None
    filtered = filter_items(
        new_items,
        settings.keywords_high,
        settings.keywords_medium,
        settings.keywords_exclude,
        window=window,
    )
    logger.info("After filter: %d items in window", len(filtered))

    deduped = dedup_near_duplicates(filtered)
    logger.info(
        "After near-dup: %d items (dropped %d)",
        len(deduped),
        len(filtered) - len(deduped),
    )

    # --- limit ---
    items = deduped[:limit] if limit else deduped
    if limit and len(deduped) > limit:
        logger.info("--limit %d: processing %d of %d items", limit, len(items), len(deduped))

    # --- GUARDRAIL 6: pre-flight cost estimate ---
    skip_claude = dry_run and settings.env.dry_run_skip_claude
    if not skip_claude and items:
        n_batches_plus_why, est_cost = cost_tracker.preflight(len(items))
        cap = cost_tracker.max_cost_inr
        logger.info(
            "About to process %d items in ~%d Claude calls. "
            "Estimated max cost: ₹%.4f | Cap: ₹%.2f",
            len(items),
            n_batches_plus_why,
            est_cost,
            cap,
        )
        if est_cost > cap * 0.5:
            logger.warning(
                "Estimated cost ₹%.4f exceeds 50%% of cap ₹%.2f — proceed carefully",
                est_cost,
                cap,
            )
    elif skip_claude:
        logger.info("Skipping Claude (DRY_RUN_SKIP_CLAUDE=true) — estimated cost: ₹0")

    # --- summarize ---
    api_key = settings.env.anthropic_api_key
    summaries = await summarize_batch(items, api_key=api_key, skip_claude=skip_claude)
    for item, summary in zip(items, summaries):
        item.summary = summary.summary
        item.category = summary.category
        item.relevance_score = summary.relevance_score

    # --- rank ---
    top3, remaining = select_top_3(items)
    logger.info("Top 3: %s", [i.title[:50] for i in top3])

    # --- why it matters ---
    why_map = await generate_why(top3, api_key=api_key, skip_claude=skip_claude)
    for item in top3:
        item.why_it_matters = why_map.get(item.id, "")

    # --- build Brief ---
    start_dt, end_dt = window if window else last_24h_window()
    brief = Brief(
        generated_at=datetime.now(timezone.utc),
        items=items,
        date_range_start=start_dt,
        date_range_end=end_dt,
        top3=top3,
    )

    # --- render ---
    html_content = render_html(brief, cost_inr=cost_tracker.estimate_cost_inr())
    plain_text = render_brief(brief)

    # --- email subject ---
    ist_now = datetime.now(IST)
    subject = f"Daily Brief – {ist_now.strftime('%d %b %Y')}"

    # --- deliver ---
    recipients = [r["address"] for r in settings.email_recipients]
    send_email(
        html=html_content,
        plain_text=plain_text,
        subject=subject,
        recipients=recipients,
        dry_run=dry_run,
        smtp_host=settings.env.smtp_host,
        smtp_port=settings.env.smtp_port,
        smtp_user=settings.env.smtp_user,
        smtp_pass=settings.env.smtp_pass,
        smtp_from=settings.env.smtp_from,
        smtp_use_tls=settings.env.smtp_use_tls,
    )

    # --- final summary ---
    cost = cost_tracker.estimate_cost_inr()
    logger.info(
        "Run complete. Items: %d | Claude calls: %d | Total cost: ₹%.4f",
        len(items),
        cost_tracker.call_count,
        cost,
    )
    if cost_tracker.call_count > 0:
        logger.info(
            "Token usage: in=%d out=%d",
            cost_tracker.input_tokens,
            cost_tracker.output_tokens,
        )
    else:
        logger.info("No Claude API calls made this run (cost: ₹0.00)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily regulatory & banking brief")
    dry_group = parser.add_mutually_exclusive_group()
    dry_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Save HTML to data/archive/ instead of sending (overrides DRY_RUN env)",
    )
    dry_group.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually send via SMTP (overrides DRY_RUN env)",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Process at most N items (cost control during testing)",
    )
    parser.add_argument(
        "--since",
        metavar="DATETIME",
        default=None,
        help='Override 24-h window start, e.g. "06 May 2026 00:00"',
    )
    args = parser.parse_args()

    # env var fallback for dry_run
    if args.dry_run is None:
        import os
        args.dry_run = os.getenv("DRY_RUN", "true").lower() != "false"

    asyncio.run(run(dry_run=args.dry_run, limit=args.limit, since=args.since))


if __name__ == "__main__":
    main()
