"""Render a Brief as Markdown.

Day 1: uses cleaned RSS description as body text (HTML stripped, entities decoded).
Day 2: will swap in Claude-generated summaries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import Brief, Item

IST = timezone(timedelta(hours=5, minutes=30))

_CAT_ORDER = {"RBI": 1, "UPI": 2, "Insurance": 3, "Markets": 4, "Banking": 5}


def _fmt_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def render_brief(brief: Brief) -> str:
    lines: list[str] = []

    lines += [
        "# Daily Regulatory & Banking Brief",
        "",
        f"**Generated:** {_fmt_ist(brief.generated_at)}",
        f"**Period:** {_fmt_ist(brief.date_range_start)} – {_fmt_ist(brief.date_range_end)}",
        f"**Items:** {len(brief.items)}",
        "",
    ]

    if not brief.items:
        lines.append("_No new items in the last 24 hours._")
        return "\n".join(lines)

    # Group by category, preserving score order within each group
    categories: dict[str, list[Item]] = {}
    for item in brief.items:
        categories.setdefault(item.category, []).append(item)

    sorted_cats = sorted(categories.keys(), key=lambda c: _CAT_ORDER.get(c, 99))

    for cat in sorted_cats:
        lines += [f"## {cat}", ""]
        for idx, item in enumerate(categories[cat], start=1):
            preview = item.body.replace("\n", " ").strip()

            # Show matched keywords next to score for debugging
            kw_str = (
                f" [{', '.join(item.matched_keywords)}]"
                if item.matched_keywords
                else ""
            )

            lines += [
                f"### {idx}. {item.title}",
                f"**Source:** `{item.source_id}` | "
                f"**Published:** {_fmt_ist(item.published_at)} | "
                f"**Score:** {item.score:.0f}{kw_str}",
                f"**URL:** {item.url}",
                "",
            ]
            if preview:
                lines += [f"> {preview}", ""]
            lines += ["---", ""]

    return "\n".join(lines)
