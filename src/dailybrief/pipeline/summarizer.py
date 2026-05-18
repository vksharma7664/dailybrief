"""Summarize news items via Claude (batched, ≤5 items per call).

Guardrails integrated:
  - skip_claude=True → returns truncated RSS descriptions, zero API calls.
  - CostCapExceeded → falls back to RSS descriptions for remaining batches.
  - Malformed JSON → one retry, then falls back to RSS description.
  - All calls go through cost_tracker.call_claude() for tracking + retry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import anthropic

from ..models import Item, Summary
from .cost_tracker import CostCapExceeded, call_claude

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-7-20250219"   # per CLAUDE.md spec
BATCH_SIZE = 5
MAX_TOKENS = 800
MAX_PARALLEL_BATCHES = 3

_PROJECT_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists()
)
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "summarize_news.txt"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _fallback_summary(item: Item) -> Summary:
    text = item.body[:200].strip()
    if len(item.body) > 200:
        text += "…"
    return Summary(summary=text or item.title, category=item.category, relevance_score=5)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json(text: str) -> str:
    """Return the best JSON candidate from *text*, handling:
    - Clean JSON arrays (most desired)
    - ```json ... ``` or ``` ... ``` code fences (Claude's common habit)
    - JSON embedded inside prose (last resort regex extraction)
    """
    text = text.strip()
    # 1. Code fence
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # 2. Already a bare array
    if text.startswith("["):
        return text
    # 3. Extract first [...] block from prose
    m = _JSON_ARRAY_RE.search(text)
    if m:
        return m.group(0)
    return text


def _parse_response(text: str, n: int) -> list[dict] | None:
    candidate = _extract_json(text)
    try:
        data = json.loads(candidate)
        if isinstance(data, list) and len(data) == n:
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Log first 120 chars so we can see what Claude actually returned
    logger.debug("JSON parse failed. Response head: %r", text[:120])
    return None


async def _summarize_one_batch(
    client: anthropic.AsyncAnthropic,
    prompt: str,
    items: list[Item],
    sem: asyncio.Semaphore,
    batch_idx: int,
) -> list[Summary]:
    items_json = json.dumps(
        [{"id": item.id, "title": item.title, "body": item.body[:600]} for item in items],
        ensure_ascii=False,
    )
    full_prompt = prompt.replace("{items_json}", items_json)

    async with sem:
        for attempt in range(2):
            try:
                response = await call_claude(
                    client,
                    context=f"summarize_batch_{batch_idx}_attempt_{attempt}",
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=[{"role": "user", "content": full_prompt}],
                )
                parsed = _parse_response(response.content[0].text, len(items))
                if parsed is not None:
                    return [
                        Summary(
                            summary=d.get("summary", ""),
                            category=d.get("category", items[i].category),
                            relevance_score=int(d.get("relevance_score", 5)),
                        )
                        for i, d in enumerate(parsed)
                    ]
                # Got a response but JSON is malformed — only retry this case
                logger.warning(
                    "Batch %d attempt %d: malformed JSON from Claude%s",
                    batch_idx,
                    attempt + 1,
                    " — retrying" if attempt == 0 else " — falling back to RSS",
                )
            except CostCapExceeded:
                raise  # propagate up so the outer loop can fall back cleanly
            except Exception as exc:
                # API error (auth, network, server) — do NOT retry; fall back immediately.
                # call_claude() already retried once for transient 5xx/rate-limit errors,
                # so anything reaching here won't be fixed by another attempt.
                logger.warning("Batch %d API error: %s — falling back to RSS immediately", batch_idx, exc)
                break

    logger.warning("Batch %d: using fallback RSS descriptions", batch_idx)
    return [_fallback_summary(item) for item in items]


async def summarize_batch(
    items: list[Item],
    api_key: str = "",
    skip_claude: bool = False,
) -> list[Summary]:
    """Summarize *items* via Claude.  Returns one Summary per item, in order.

    If *skip_claude* is True (dry-run default), returns RSS truncations with
    zero API calls and zero cost.
    """
    if not items:
        return []

    if skip_claude:
        logger.info("Skipping Claude summarization (SKIP_CLAUDE_SUMMARIZATION=true)")
        return [_fallback_summary(item) for item in items]

    prompt = _load_prompt()
    client = anthropic.AsyncAnthropic(api_key=api_key or None)
    sem = asyncio.Semaphore(MAX_PARALLEL_BATCHES)
    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]

    tasks = [
        _summarize_one_batch(client, prompt, batch, sem, idx)
        for idx, batch in enumerate(batches)
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[Summary] = []
    for batch, outcome in zip(batches, raw):
        if isinstance(outcome, CostCapExceeded):
            logger.error("Cost cap hit during summarization: %s — using RSS fallback", outcome)
            results.extend(_fallback_summary(item) for item in batch)
        elif isinstance(outcome, Exception):
            logger.error("Batch failed: %s — using RSS fallback", outcome)
            results.extend(_fallback_summary(item) for item in batch)
        else:
            results.extend(outcome)

    return results
