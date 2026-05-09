"""Generate a one-sentence "why it matters" for each of the top-3 items.

Each item gets its own Claude call (not batched) because the prompt is
per-item and short.  Guardrails: skip_claude, CostCapExceeded fallback.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import anthropic

from ..models import Item
from .cost_tracker import CostCapExceeded, call_claude
from .summarizer import MODEL

logger = logging.getLogger(__name__)

MAX_TOKENS = 100

_PROJECT_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists()
)
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "why_it_matters.txt"

_DRY_RUN_PLACEHOLDER = "[dry-run placeholder]"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _fmt_item(item: Item) -> str:
    return (
        f"Title: {item.title}\n"
        f"Summary: {item.summary or item.body[:300]}\n"
        f"Category: {item.category}\n"
        f"URL: {item.url}"
    )


async def _why_one(
    client: anthropic.AsyncAnthropic,
    prompt_template: str,
    item: Item,
    idx: int,
) -> tuple[str, str]:
    """Return (item.id, why_text).  Falls back to placeholder on any error."""
    full_prompt = prompt_template.replace("{item}", _fmt_item(item))
    try:
        response = await call_claude(
            client,
            context=f"why_it_matters_{idx}",
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": full_prompt}],
        )
        text = response.content[0].text.strip()
        return item.id, text
    except CostCapExceeded:
        raise
    except Exception as exc:
        logger.warning("why_it_matters for item %r failed: %s — using fallback", item.id[:8], exc)
        return item.id, _DRY_RUN_PLACEHOLDER


async def generate_why(
    items: list[Item],
    api_key: str = "",
    skip_claude: bool = False,
) -> dict[str, str]:
    """Return {item.id: why_text} for each item in *items* (typically top-3).

    If *skip_claude* is True, returns placeholder text with zero API calls.
    CostCapExceeded causes remaining items to receive the placeholder.
    """
    if not items:
        return {}

    if skip_claude:
        logger.info("Skipping Claude why-it-matters (SKIP_CLAUDE_SUMMARIZATION=true)")
        return {item.id: _DRY_RUN_PLACEHOLDER for item in items}

    prompt_template = _load_prompt()
    client = anthropic.AsyncAnthropic(api_key=api_key or None)

    result: dict[str, str] = {}
    for idx, item in enumerate(items):
        try:
            item_id, text = await _why_one(client, prompt_template, item, idx)
            result[item_id] = text
        except CostCapExceeded as exc:
            logger.error("Cost cap hit during why-it-matters: %s — placeholders for remaining", exc)
            for remaining in items[idx:]:
                result[remaining.id] = _DRY_RUN_PLACEHOLDER
            break

    return result
