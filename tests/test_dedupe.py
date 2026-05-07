"""Tests for SQLite-backed dedupe store."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from dailybrief.models import Item
from dailybrief.pipeline.dedupe import DedupeStore


def _make_item(title: str, body: str = "default body text") -> Item:
    content_hash = hashlib.sha256(
        (title.lower().strip() + body[:500]).encode()
    ).hexdigest()
    return Item(
        id=content_hash,
        title=title,
        url="https://rbi.org.in/example",
        body=body,
        category="RBI",
        source_id="rbi_press",
        priority=1,
        published_at=datetime.now(timezone.utc),
        content_hash=content_hash,
    )


def test_new_item_is_not_seen(tmp_path):
    with DedupeStore(tmp_path / "test.db") as store:
        item = _make_item("Circular on CKYC requirements")
        assert not store.is_seen(item)


def test_mark_seen_makes_item_seen(tmp_path):
    with DedupeStore(tmp_path / "test.db") as store:
        item = _make_item("Circular on CKYC requirements")
        store.mark_seen(item)
        assert store.is_seen(item)


def test_filter_new_first_run_returns_all(tmp_path):
    items = [_make_item(f"Item {i}") for i in range(3)]
    with DedupeStore(tmp_path / "test.db") as store:
        new = store.filter_new(items)
    assert len(new) == 3


def test_filter_new_second_run_returns_none(tmp_path):
    items = [_make_item(f"Item {i}") for i in range(3)]
    db = tmp_path / "test.db"
    with DedupeStore(db) as store:
        store.filter_new(items)  # first run marks all seen
    with DedupeStore(db) as store:
        new = store.filter_new(items)  # second run: all duplicates
    assert new == []


def test_filter_new_only_new_items_pass(tmp_path):
    db = tmp_path / "test.db"
    item_old = _make_item("Old circular about PPI")
    item_new = _make_item("New circular about eKYC")

    with DedupeStore(db) as store:
        store.mark_seen(item_old)

    with DedupeStore(db) as store:
        result = store.filter_new([item_old, item_new])

    assert len(result) == 1
    assert result[0].title == "New circular about eKYC"


def test_different_titles_same_body_are_different(tmp_path):
    """Different title → different hash → both pass dedupe."""
    body = "same body content"
    item_a = _make_item("Title Alpha", body)
    item_b = _make_item("Title Beta", body)
    assert item_a.content_hash != item_b.content_hash

    with DedupeStore(tmp_path / "test.db") as store:
        store.mark_seen(item_a)
        assert not store.is_seen(item_b)


def test_same_title_truncated_body_matches(tmp_path):
    """Hash uses only first 500 chars of body — long bodies still match."""
    short_body = "x" * 500          # exactly 500 chars
    long_body = short_body + "extra content appended beyond the 500-char boundary" * 5
    item_a = _make_item("Same title", short_body)
    item_b = _make_item("Same title", long_body)
    # body[:500] is identical for both → same hash
    assert item_a.content_hash == item_b.content_hash
