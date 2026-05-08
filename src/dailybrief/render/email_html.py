"""Render a Brief as an HTML email using the Jinja2 template."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import Brief, Item

IST = timezone(timedelta(hours=5, minutes=30))

_PROJECT_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists()
)
_TEMPLATE_DIR = _PROJECT_ROOT / "templates"


def _fmt_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def _group_by_category(items: list[Item]) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {}
    for item in items:
        out.setdefault(item.category, []).append(item)
    return out


def render_html(brief: Brief, cost_inr: float = 0.0) -> str:
    """Return the full HTML email string."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["datefmt"] = _fmt_ist

    template = env.get_template("email.html.j2")

    top3_ids = {item.id for item in brief.top3}
    remaining = [item for item in brief.items if item.id not in top3_ids]

    return template.render(
        top3=brief.top3,
        remaining_by_category=_group_by_category(remaining),
        generated_at=_fmt_ist(brief.generated_at),
        date_range_start=_fmt_ist(brief.date_range_start),
        date_range_end=_fmt_ist(brief.date_range_end),
        total_items=len(brief.items),
        cost_inr=f"{cost_inr:.4f}",
    )
