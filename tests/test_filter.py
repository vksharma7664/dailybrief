"""Tests for the 24-h IST filter and keyword scorer."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from dailybrief.models import Item
from dailybrief.pipeline.filter import (
    _compile_keyword,
    _jaccard,
    _title_tokens,
    dedup_near_duplicates,
    filter_items,
    is_within_window,
    last_24h_window,
    score_item,
    should_exclude,
    to_ist,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _make_item(
    title: str,
    body: str = "",
    category: str = "RBI",
    priority: int = 1,
    hours_ago: float = 1.0,
) -> Item:
    published_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    content_hash = hashlib.sha256(
        (title.lower().strip() + body[:500]).encode()
    ).hexdigest()
    return Item(
        id=content_hash,
        title=title,
        url="https://example.com",
        body=body,
        category=category,
        source_id="test",
        priority=priority,
        published_at=published_at,
        content_hash=content_hash,
    )


# ---------------------------------------------------------------------------
# Window tests
# ---------------------------------------------------------------------------

def test_item_1h_old_is_in_window():
    start, end = last_24h_window()
    assert is_within_window(_make_item("Recent", hours_ago=1), start, end)


def test_item_23h_old_is_in_window():
    start, end = last_24h_window()
    assert is_within_window(_make_item("Nearly old", hours_ago=23), start, end)


def test_item_25h_old_is_outside_window():
    start, end = last_24h_window()
    assert not is_within_window(_make_item("Old", hours_ago=25), start, end)


def test_ist_conversion_correct():
    utc_dt = datetime(2025, 5, 6, 0, 0, 0, tzinfo=timezone.utc)
    ist_dt = to_ist(utc_dt)
    assert ist_dt.hour == 5
    assert ist_dt.minute == 30


# ---------------------------------------------------------------------------
# Exclusion tests (word-boundary)
# ---------------------------------------------------------------------------

def test_exclude_bollywood():
    item = _make_item("Bollywood actor promotes banking")
    assert should_exclude(item, ["bollywood"])


def test_exclude_cricket():
    item = _make_item("Economy update", "cricket season boosts consumer spending")
    assert should_exclude(item, ["cricket"])


def test_no_exclusion_for_clean_item():
    item = _make_item("RBI circular on PPI guidelines")
    assert not should_exclude(item, ["bollywood", "cricket"])


def test_exclusion_is_case_insensitive():
    item = _make_item("BOLLYWOOD Star Opens Bank Account")
    assert should_exclude(item, ["bollywood"])


def test_exclusion_uses_word_boundary():
    """'crick' should not be excluded just because 'cricket' is an exclude keyword."""
    item = _make_item("Brick-and-mortar banking")
    assert not should_exclude(item, ["cricket"])


# ---------------------------------------------------------------------------
# Scoring tests — BUG 3 fixed: base=1, word-boundary, returns (score, keywords)
# ---------------------------------------------------------------------------

def test_base_score_is_1_with_no_keywords():
    item = _make_item("Generic banking news with no matching keywords")
    score, matched = score_item(item, high_priority=[], medium_priority=[])
    assert score == 1.0
    assert matched == []


def test_high_priority_keyword_adds_10():
    item = _make_item("New PPI guidelines issued by RBI")
    score, matched = score_item(item, high_priority=["PPI"], medium_priority=[])
    assert score == 11.0  # 1 base + 10
    assert "PPI" in matched


def test_medium_priority_keyword_adds_5():
    item = _make_item("UPI transaction volume hits record", priority=2)
    score, matched = score_item(item, high_priority=[], medium_priority=["UPI"])
    assert score == 6.0   # 1 base + 5
    assert "UPI" in matched


def test_multiple_keyword_hits_accumulate():
    item = _make_item("PPI and KYC circular from RBI", "UPI onboarding update")
    score, matched = score_item(
        item,
        high_priority=["PPI"],
        medium_priority=["KYC", "UPI", "onboarding"],
    )
    assert score == 1 + 10 + 5 + 5 + 5  # 26
    assert set(matched) == {"PPI", "KYC", "UPI", "onboarding"}


def test_score_uses_word_boundary():
    """'PPID' must not match keyword 'PPI' when using word boundaries."""
    item = _make_item("PPID system launch — new identifier")
    score, matched = score_item(item, high_priority=["PPI"], medium_priority=[])
    assert "PPI" not in matched
    assert score == 1.0


def test_score_is_case_insensitive():
    item = _make_item("ppi framework updated")
    score, matched = score_item(item, high_priority=["PPI"], medium_priority=[])
    assert score == 11.0
    assert "PPI" in matched


def test_no_match_scores_1_not_6():
    """Regression: items with no keyword hits must score 1, not 6."""
    item = _make_item("Macroeconomic overview for April 2025", priority=1)
    score, matched = score_item(item, high_priority=["PPI"], medium_priority=["UPI"])
    assert score == 1.0
    assert matched == []


# ---------------------------------------------------------------------------
# End-to-end filter tests
# ---------------------------------------------------------------------------

def test_filter_removes_excluded_items():
    items = [
        _make_item("PPI circular", hours_ago=2),
        _make_item("Bollywood banking news", hours_ago=2),
    ]
    result = filter_items(items, ["PPI"], [], ["bollywood"])
    assert len(result) == 1
    assert "PPI" in result[0].title


def test_filter_removes_old_items():
    items = [
        _make_item("Recent circular", hours_ago=2),
        _make_item("Old circular", hours_ago=30),
    ]
    result = filter_items(items, [], [], [])
    assert len(result) == 1
    assert "Recent" in result[0].title


def test_filter_sorts_by_score_descending():
    items = [
        _make_item("Generic banking update", hours_ago=2, priority=2),
        _make_item("PPI and CKYC mandate update", hours_ago=2, priority=1),
    ]
    result = filter_items(items, ["PPI", "CKYC"], [], [])
    assert len(result) == 2
    assert result[0].score >= result[1].score
    assert "PPI" in result[0].title


def test_filter_sets_matched_keywords_on_item():
    items = [_make_item("PPI circular", hours_ago=1)]
    result = filter_items(items, ["PPI"], ["KYC"], [])
    assert result[0].matched_keywords == ["PPI"]


def test_cricket_excluded_from_filter():
    items = [
        _make_item("RBI policy note", hours_ago=1),
        _make_item("Cricket season banking", hours_ago=1),
    ]
    result = filter_items(items, [], [], ["cricket"])
    assert len(result) == 1
    assert "RBI" in result[0].title


def test_filter_empty_input():
    assert filter_items([], [], [], []) == []


# ---------------------------------------------------------------------------
# Spelling normalisation tests (Issue 2)
# ---------------------------------------------------------------------------

def test_authorised_matches_authorized():
    """Keyword 'Authorised Persons' must match 'Authorized Persons' (US spelling)."""
    item = _make_item("Authorized Persons Regulations 2026")
    score, matched = score_item(item, high_priority=["Authorised Persons"], medium_priority=[])
    assert score == 11.0
    assert "Authorised Persons" in matched


def test_authorised_matches_british():
    """Keyword 'Authorised Persons' must match itself (British spelling)."""
    item = _make_item("Authorised Persons Framework Updated")
    score, matched = score_item(item, high_priority=["Authorised Persons"], medium_priority=[])
    assert score == 11.0


def test_fema_keyword_matches():
    item = _make_item("FEMA (Authorised Persons) Regulations 2026")
    score, matched = score_item(item, high_priority=["FEMA", "Authorised Persons"], medium_priority=[])
    assert score == 21.0   # 1 + 10 + 10
    assert set(matched) == {"FEMA", "Authorised Persons"}


def test_foreign_exchange_management_matches():
    item = _make_item("Foreign Exchange Management Regulations Issued")
    score, matched = score_item(
        item, high_priority=["Foreign Exchange Management"], medium_priority=[]
    )
    assert score == 11.0


def test_ad_ii_hyphen_variant():
    """AD-II (hyphen) and AD II (space) are separate keywords — both should hit."""
    item = _make_item("Guidelines for AD-II and AD II operations")
    score, matched = score_item(item, high_priority=["AD II", "AD-II"], medium_priority=[])
    assert "AD II" in matched
    assert "AD-II" in matched
    assert score == 21.0


def test_compile_keyword_cache():
    """_compile_keyword is cached — same object returned for same input."""
    p1 = _compile_keyword("Authorised Persons")
    p2 = _compile_keyword("Authorised Persons")
    assert p1 is p2


# ---------------------------------------------------------------------------
# Near-duplicate dedup tests (Issue 3)
# ---------------------------------------------------------------------------

def test_title_tokens_removes_stopwords():
    tokens = _title_tokens("The Reserve Bank of India Issues Circular")
    assert "the" not in tokens
    assert "reserve" not in tokens
    assert "rbi" not in tokens
    assert "issues" in tokens
    assert "circular" in tokens


def test_jaccard_identical():
    a = frozenset(["ppi", "circular", "guidelines"])
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    a = frozenset(["ppi"])
    b = frozenset(["upi"])
    assert _jaccard(a, b) == 0.0


def test_jaccard_partial():
    a = frozenset(["ppi", "circular", "guidelines"])
    b = frozenset(["ppi", "circular", "notification"])
    # |intersection|=2, |union|=4 → 0.5
    assert abs(_jaccard(a, b) - 0.5) < 0.001


def test_near_dup_drops_lower_score():
    high = _make_item("PPI Circular issued by RBI guidelines", hours_ago=1)
    high.score = 21.0
    low = _make_item("PPI Circular RBI guidelines issued", hours_ago=1)
    low.score = 1.0
    result = dedup_near_duplicates([high, low], threshold=0.8)
    assert len(result) == 1
    assert result[0].score == 21.0


def test_near_dup_keeps_both_dissimilar():
    item1 = _make_item("FEMA Authorised Persons Regulations 2026", hours_ago=1)
    item2 = _make_item("Treasury Bill auction result published", hours_ago=1)
    result = dedup_near_duplicates([item1, item2], threshold=0.8)
    assert len(result) == 2


def test_near_dup_empty_input():
    assert dedup_near_duplicates([]) == []


def test_near_dup_single_item():
    item = _make_item("Only item", hours_ago=1)
    assert dedup_near_duplicates([item]) == [item]
