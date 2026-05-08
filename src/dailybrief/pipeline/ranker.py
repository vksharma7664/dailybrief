"""Select the top-3 items by combined keyword + Claude relevance score.

Formula: (keyword_score * 0.7) + (claude_relevance * 3 * 0.3)

On ties, prefer items with a LATER published_at (today's circular beats
yesterday's).
"""
from __future__ import annotations

from ..models import Item


def _combined(item: Item) -> float:
    return (item.score * 0.7) + (item.relevance_score * 3 * 0.3)


def select_top_3(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Return (top3, remaining), both in descending combined-score order."""
    ranked = sorted(
        items,
        key=lambda x: (_combined(x), x.published_at.timestamp()),
        reverse=True,
    )
    return ranked[:3], ranked[3:]
