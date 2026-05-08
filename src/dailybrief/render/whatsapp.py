"""Format a Brief as a WhatsApp-compatible plain-text message (≤4000 chars)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import Brief

IST = timezone(timedelta(hours=5, minutes=30))
MAX_CHARS = 4000


def render_whatsapp(brief: Brief, archive_url: str = "") -> str:
    """Return a WhatsApp message ≤4000 chars.

    Includes date header, top-3 items with summaries and why-it-matters,
    and an optional link to the full HTML brief.
    """
    ist_now = brief.generated_at.astimezone(IST)
    lines: list[str] = [
        f"*Daily Brief – {ist_now.strftime('%d %b %Y')}*",
        f"_{len(brief.items)} items today | Top {len(brief.top3)} highlights:_",
        "",
    ]

    for i, item in enumerate(brief.top3, 1):
        lines.append(f"*{i}. {item.title}*")
        if item.summary:
            lines.append(item.summary)
        if item.why_it_matters:
            lines.append(f"→ _{item.why_it_matters}_")
        lines.append(item.url)
        lines.append("")

    if archive_url:
        lines.append(f"Full brief: {archive_url}")

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 1] + "…"
    return text
