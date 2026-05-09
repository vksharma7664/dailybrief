"""Orchestrator — daily regulatory & banking brief pipeline.

Pipeline:
  fetch (RSS + HTML) → full-text enrich (RBI PDFs) →
  dedup → filter (24h + keyword score) → [limit N] →
  preflight estimate → summarize (Claude batch) →
  rank (top 3) → why it matters →
  render HTML email → deliver email →
  render WhatsApp → deliver WhatsApp

CLI flags:
  --dry-run / --no-dry-run      override DRY_RUN env var
  --skip-dedupe                 bypass SQLite dedupe (useful for testing/backfill)
  --limit N                     cap items processed (cost control)
  --since "06 May 2026 00:00"   override 24-h window start (backfill)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

from .delivery.email import send_email, send_health_alert
from .delivery.whatsapp import send_whatsapp
from .fetchers.html_irdai import IRDAIFetcher
from .fetchers.html_npci import NPCIFetcher
from .fetchers.html_sebi import SEBIFetcher
from .fetchers.pdf import enrich_full_text
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
from .render.whatsapp import render_whatsapp
from .settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

_SINCE_FMT = "%d %b %Y %H:%M"

_HTML_FETCHER_MAP = {
    "html_npci": NPCIFetcher,
    "html_sebi": SEBIFetcher,
    "html_irdai": IRDAIFetcher,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _empty_fetch() -> list:
    """No-op coroutine — used when no fetcher exists for an HTML source."""
    return []


def _setup_file_logger() -> None:
    """Add a rotating file handler so prod runs have a persistent log trail."""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "dailybrief.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB per file
        backupCount=14,              # ~2 weeks of daily runs
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    ))
    logging.getLogger().addHandler(handler)


def _parse_since(s: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(s, _SINCE_FMT).replace(tzinfo=IST)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--since must be like '06 May 2026 00:00', got: {s!r}"
        )
    return start, datetime.now(IST)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run(
    dry_run: bool,
    limit: int | None,
    since: str | None,
    skip_dedupe: bool = False,
) -> None:
    settings = Settings()
    cost_tracker.reset()

    rate_limiter = RateLimiter(
        rate=settings.source_defaults.get("rate_limit_per_domain", 1.0)
    )
    common_kwargs = dict(
        rate_limiter=rate_limiter,
        timeout=float(settings.source_defaults.get("timeout_seconds", 30)),
        retries=int(settings.source_defaults.get("retries", 3)),
        user_agent=settings.source_defaults.get(
            "user_agent", "DailyBrief/1.0 (internal compliance tool)"
        ),
    )

    rss_fetcher = RSSFetcher(**common_kwargs)
    rss_sources = [s for s in settings.sources if s.type == "rss"]
    html_sources = [s for s in settings.sources if s.type == "html"]

    logger.info("Fetching %d RSS + %d HTML sources…", len(rss_sources), len(html_sources))

    # --- RSS fetch ---
    rss_tasks = [
        rss_fetcher.fetch(s.id, s.url, s.category, s.priority) for s in rss_sources
    ]

    # --- HTML fetch ---
    html_tasks = []
    for source in html_sources:
        fetcher_cls = _HTML_FETCHER_MAP.get(source.parser)
        if fetcher_cls is None:
            logger.warning("[%s] Unknown HTML parser: %r — skipping", source.id, source.parser)
            html_tasks.append(_empty_fetch())
            continue
        fetcher = fetcher_cls(**common_kwargs)
        html_tasks.append(fetcher.fetch(source.id, source.url, source.category, source.priority))

    all_results = await asyncio.gather(*rss_tasks, *html_tasks, return_exceptions=True)
    rss_results = all_results[: len(rss_sources)]
    html_results = all_results[len(rss_sources):]

    all_items = []
    failed_sources: list[tuple[str, str]] = []   # (source_id, error_msg) — for health alert

    for source, result in zip(rss_sources, rss_results):
        if isinstance(result, Exception):
            logger.error("[%s] Fetch error: %s", source.id, result)
            failed_sources.append((source.id, str(result)))
        else:
            logger.info("[%s] %d items fetched", source.id, len(result))
            all_items.extend(result)

    for source, result in zip(html_sources, html_results):
        if isinstance(result, Exception):
            logger.error("[%s] HTML fetch error: %s", source.id, result)
            failed_sources.append((source.id, str(result)))
        else:
            logger.info("[%s] %d items fetched", source.id, len(result))
            all_items.extend(result)

    logger.info("Total fetched: %d", len(all_items))

    # --- Fail-closed: health alert on any source failure ---
    if failed_sources:
        logger.warning("%d source(s) failed — sending health alert", len(failed_sources))
        send_health_alert(
            failed_sources,
            recipients=[r["address"] for r in settings.email_recipients],
            smtp_host=settings.env.smtp_host,
            smtp_port=settings.env.smtp_port,
            smtp_user=settings.env.smtp_user,
            smtp_pass=settings.env.smtp_pass,
            smtp_from=settings.env.smtp_from,
            smtp_use_tls=settings.env.smtp_use_tls,
        )

    # --- Full-text enrichment for RBI notification links (PDFs / HTML) ---
    full_text_items = [
        item for item in all_items
        if any(s.id == item.source_id and s.fetch_full_text for s in settings.sources)
    ]
    if full_text_items:
        logger.info("Enriching full text for %d RBI notification items…", len(full_text_items))
        await enrich_full_text(
            full_text_items,
            rate_limiter=rate_limiter,
            user_agent=common_kwargs["user_agent"],
        )

    # --- Dedupe (SQLite content-hash) ---
    if skip_dedupe:
        logger.warning(
            "--skip-dedupe: bypassing SQLite deduplication — all %d fetched items "
            "will be processed; nothing will be marked as seen this run",
            len(all_items),
        )
        new_items = all_items
    else:
        db_path = Path("data/seen_dryrun.db" if dry_run else "data/seen.db")
        with DedupeStore(db_path) as store:
            new_items = store.filter_new(all_items)
        logger.info("After dedupe: %d new items", len(new_items))

    # --- Filter ---
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
        "After near-dup: %d items (dropped %d)", len(deduped), len(filtered) - len(deduped)
    )

    # --- Limit ---
    items = deduped[:limit] if limit else deduped
    if limit and len(deduped) > limit:
        logger.info("--limit %d: processing %d of %d items", limit, len(items), len(deduped))

    # --- Preflight cost estimate ---
    skip_claude = settings.env.skip_claude_summarization
    skip_why   = settings.env.skip_why_it_matters
    if not skip_claude and items:
        n_calls, est_cost = cost_tracker.preflight(len(items))
        cap = cost_tracker.max_cost_inr
        logger.info(
            "About to process %d items in ~%d Claude calls. Est max cost: ₹%.4f | Cap: ₹%.2f",
            len(items), n_calls, est_cost, cap,
        )
        if est_cost > cap * 0.5:
            logger.warning(
                "Estimated cost ₹%.4f exceeds 50%% of cap ₹%.2f — proceed carefully",
                est_cost, cap,
            )
    elif skip_claude:
        logger.info("Skipping Claude (SKIP_CLAUDE_SUMMARIZATION=true) — estimated cost: ₹0")

    # --- Summarize ---
    api_key = settings.env.anthropic_api_key
    summaries = await summarize_batch(items, api_key=api_key, skip_claude=skip_claude)
    for item, summary in zip(items, summaries):
        item.summary = summary.summary
        item.category = summary.category
        item.relevance_score = summary.relevance_score

    # --- Rank ---
    top3, remaining = select_top_3(items)
    logger.info("Top 3: %s", [i.title[:50] for i in top3])

    # --- Why it matters ---
    why_map = await generate_why(top3, api_key=api_key, skip_claude=skip_why)
    for item in top3:
        item.why_it_matters = why_map.get(item.id, "")

    # --- Build Brief ---
    start_dt, end_dt = window if window else last_24h_window()
    brief = Brief(
        generated_at=datetime.now(timezone.utc),
        items=items,
        date_range_start=start_dt,
        date_range_end=end_dt,
        top3=top3,
    )

    # --- Render ---
    html_content = render_html(brief, cost_inr=cost_tracker.estimate_cost_inr())
    plain_text = render_brief(brief)
    ist_now = datetime.now(IST)
    subject = f"Daily Brief – {ist_now.strftime('%d %b %Y')}"

    # --- Email delivery ---
    recipients = [r["address"] for r in settings.email_recipients]
    _ok, archive_path = send_email(
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

    # Build the archive URL from the file path + configured base URL
    archive_url = (
        f"{settings.env.archive_base_url.rstrip('/')}/{archive_path.name}"
        if archive_path and settings.env.archive_base_url
        else ""
    )

    # --- WhatsApp delivery ---
    wa_recipients = [
        r["number"] for r in settings.whatsapp_recipients if r.get("enabled", True)
    ]
    if wa_recipients:
        wa_message = render_whatsapp(brief, archive_url=archive_url)
        send_whatsapp(
            message=wa_message,
            recipients=wa_recipients,
            account_sid=settings.env.twilio_account_sid,
            auth_token=settings.env.twilio_auth_token,
            from_number=settings.env.twilio_whatsapp_from,
            dry_run=dry_run,
        )

    # --- Final summary ---
    cost = cost_tracker.estimate_cost_inr()
    logger.info(
        "Run complete. Items: %d | Claude calls: %d | Total cost: ₹%.4f",
        len(items), cost_tracker.call_count, cost,
    )
    if cost_tracker.call_count > 0:
        logger.info(
            "Token usage: in=%d out=%d",
            cost_tracker.input_tokens,
            cost_tracker.output_tokens,
        )
    else:
        logger.info("No Claude API calls made this run (cost: ₹0.00)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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
        "--skip-dedupe",
        action="store_true",
        default=False,
        help=(
            "Bypass SQLite deduplication — every fetched item is processed regardless "
            "of whether it was seen before. Nothing is marked seen this run. "
            "Useful for testing, backfill, and force-resends."
        ),
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

    if args.dry_run is None:
        import os
        args.dry_run = os.getenv("DRY_RUN", "true").lower() != "false"

    _setup_file_logger()

    asyncio.run(
        run(
            dry_run=args.dry_run,
            limit=args.limit,
            since=args.since,
            skip_dedupe=args.skip_dedupe,
        )
    )


if __name__ == "__main__":
    main()
