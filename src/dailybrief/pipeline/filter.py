"""Filter and score items.

Rules:
- Keep only items published in the last-24h IST window.
- Drop items matching any exclude keyword (word-boundary, case-insensitive).
- Score items: base 1 + keyword hits (+10 high, +5 medium, word-boundary).
- Return matched keywords alongside score for debugging.
- Sort descending by score.
- Near-duplicate dedup via Jaccard on title tokens (within a single run).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from ..models import Item

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
IST = timezone(IST_OFFSET)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def last_24h_window() -> tuple[datetime, datetime]:
    """Return (start, end) as IST-aware datetimes."""
    end = datetime.now(IST)
    start = end - timedelta(hours=24)
    return start, end


def is_within_window(item: Item, start: datetime, end: datetime) -> bool:
    pub_ist = to_ist(item.published_at)
    return start <= pub_ist <= end


# ---------------------------------------------------------------------------
# Keyword matching — compiled patterns with spelling normalisation
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _compile_keyword(keyword: str) -> re.Pattern[str]:
    """
    Build a compiled regex for *keyword*:
    - "Word-safe" keywords (letters/digits/spaces/hyphens): wrapped in \\b..\\b
    - Punctuated keywords (dots, parens — e.g. "A.P. (DIR Series)"): literal
      substring match (no \\b anchors, which don't survive punctuation)
    - British→American spelling: "ised" becomes "(?:ised|ized)"
    - All matches are case-insensitive
    - Multi-word keywords allow flexible whitespace between tokens
    """
    kw = keyword.strip().lower()
    is_word_safe = bool(re.match(r"^[a-z0-9\s\-]+$", kw))

    # Escape each token separately, then rejoin with flexible whitespace
    parts = [re.escape(w) for w in kw.split()]
    joined = r"\s+".join(parts)

    # British→American spelling normalisation
    joined = joined.replace("ised", "(?:ised|ized)")

    if is_word_safe:
        return re.compile(r"\b" + joined + r"\b", re.IGNORECASE)
    return re.compile(joined, re.IGNORECASE)


def _word_match(keyword: str, text: str) -> bool:
    """Return True if *keyword* appears in *text* (word-boundary, case-insensitive)."""
    return bool(_compile_keyword(keyword).search(text))


def should_exclude(item: Item, exclude_keywords: list[str]) -> bool:
    text = (item.title + " " + item.body).lower()
    return any(_word_match(kw, text) for kw in exclude_keywords)


def score_item(
    item: Item,
    high_priority: list[str],
    medium_priority: list[str],
) -> tuple[float, list[str]]:
    """Return (score, matched_keywords). Base score is 1."""
    text = (item.title + " " + item.body).lower()
    score = 1.0
    matched: list[str] = []

    for kw in high_priority:
        if _word_match(kw, text):
            score += 10.0
            matched.append(kw)

    for kw in medium_priority:
        if _word_match(kw, text):
            score += 5.0
            matched.append(kw)

    return score, matched


# ---------------------------------------------------------------------------
# Near-duplicate deduplication (within a single run)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "of", "and", "in", "to", "for", "by", "on",
    "is", "are", "was", "were", "that", "which", "with", "at", "from",
    "as", "be", "or", "not", "its", "it", "this", "has", "have",
    "under", "s", "reserve", "bank", "india", "rbi",
})


def _title_tokens(title: str) -> frozenset[str]:
    """Lowercase word tokens with stopwords and short words removed."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def dedup_near_duplicates(
    items: list[Item], threshold: float = 0.8
) -> list[Item]:
    """
    O(n²) within-run dedup: if two items share ≥80% Jaccard similarity on
    title tokens, drop the lower-scoring one (or the later one on a tie).
    Dropped items are logged at INFO level for review.
    """
    tokens = [_title_tokens(item.title) for item in items]
    dropped: set[int] = set()

    for i in range(len(items)):
        if i in dropped:
            continue
        for j in range(i + 1, len(items)):
            if j in dropped:
                continue
            sim = _jaccard(tokens[i], tokens[j])
            if sim >= threshold:
                # Keep the higher-scoring item; tie → keep first (i)
                if items[i].score >= items[j].score:
                    loser, winner = j, i
                else:
                    loser, winner = i, j
                logger.info(
                    f"Near-dup [{sim:.0%}] drop {items[loser].source_id!r}: "
                    f"{items[loser].title[:60]!r} "
                    f"(kept {items[winner].source_id!r}: {items[winner].title[:40]!r})"
                )
                dropped.add(loser)
                if loser == i:
                    break  # i is gone; no point comparing further

    return [item for idx, item in enumerate(items) if idx not in dropped]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def filter_items(
    items: list[Item],
    high_priority: list[str],
    medium_priority: list[str],
    exclude_keywords: list[str],
) -> list[Item]:
    start, end = last_24h_window()
    result: list[Item] = []

    for item in items:
        if should_exclude(item, exclude_keywords):
            logger.debug(f"Excluded (keyword): {item.title[:80]!r}")
            continue
        if not is_within_window(item, start, end):
            logger.debug(
                f"Excluded (time): {item.title[:80]!r} pub={item.published_at.isoformat()}"
            )
            continue
        item.score, item.matched_keywords = score_item(item, high_priority, medium_priority)
        result.append(item)

    result.sort(key=lambda x: x.score, reverse=True)
    return result
