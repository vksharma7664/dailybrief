"""Tests for the top-3 ranker.

Pure function tests — no Claude calls, no mocking needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dailybrief.models import Item
from dailybrief.pipeline.ranker import _combined, select_top_3

_BASE_DT = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)


def _item(n: int, score: float, relevance: int, hours_ago: float = 0.0) -> Item:
    return Item(
        id=f"id{n}",
        title=f"Item {n}",
        url=f"https://example.com/{n}",
        body="body",
        category="RBI",
        source_id="test",
        priority=2,
        published_at=_BASE_DT - timedelta(hours=hours_ago),
        content_hash=f"id{n}",
        score=score,
        relevance_score=relevance,
    )


# ---------------------------------------------------------------------------
# Combined score formula: (kw * 0.7) + (rel * 3 * 0.3)
# ---------------------------------------------------------------------------

def test_combined_score_formula():
    item = _item(1, score=10.0, relevance=8)
    # (10 * 0.7) + (8 * 3 * 0.3) = 7 + 7.2 = 14.2
    assert abs(_combined(item) - 14.2) < 1e-9


def test_combined_score_zero_relevance():
    item = _item(1, score=5.0, relevance=0)
    assert abs(_combined(item) - 3.5) < 1e-9  # 5 * 0.7


def test_combined_score_zero_keyword():
    item = _item(1, score=1.0, relevance=10)  # base score = 1
    # (1 * 0.7) + (10 * 3 * 0.3) = 0.7 + 9 = 9.7
    assert abs(_combined(item) - 9.7) < 1e-9


# ---------------------------------------------------------------------------
# select_top_3
# ---------------------------------------------------------------------------

def test_returns_correct_top3():
    items = [
        _item(1, score=20.0, relevance=5),   # combined: 14 + 4.5 = 18.5
        _item(2, score=5.0, relevance=10),   # combined: 3.5 + 9 = 12.5
        _item(3, score=15.0, relevance=8),   # combined: 10.5 + 7.2 = 17.7
        _item(4, score=1.0, relevance=2),    # combined: 0.7 + 1.8 = 2.5
        _item(5, score=10.0, relevance=6),   # combined: 7 + 5.4 = 12.4
    ]
    top3, remaining = select_top_3(items)

    assert len(top3) == 3
    assert len(remaining) == 2
    assert top3[0].id == "id1"   # 18.5
    assert top3[1].id == "id3"   # 17.7
    assert top3[2].id == "id2"   # 12.5
    assert {i.id for i in remaining} == {"id4", "id5"}


def test_fewer_than_3_items():
    items = [_item(1, score=10.0, relevance=5), _item(2, score=5.0, relevance=3)]
    top3, remaining = select_top_3(items)
    assert len(top3) == 2
    assert len(remaining) == 0


def test_empty_list():
    top3, remaining = select_top_3([])
    assert top3 == []
    assert remaining == []


def test_tie_broken_by_later_publish_time():
    # Same combined score → later published_at wins (smaller hours_ago)
    early = _item(1, score=10.0, relevance=5, hours_ago=24.0)  # yesterday
    late = _item(2, score=10.0, relevance=5, hours_ago=1.0)    # 1h ago
    # Both have same combined score

    top3, _ = select_top_3([early, late, _item(3, score=1.0, relevance=1)])
    # 'late' should rank above 'early' because it was published more recently
    assert top3[0].id == "id2"
    assert top3[1].id == "id1"


def test_all_items_same_score():
    items = [_item(i, score=5.0, relevance=5) for i in range(6)]
    top3, remaining = select_top_3(items)
    assert len(top3) == 3
    assert len(remaining) == 3
