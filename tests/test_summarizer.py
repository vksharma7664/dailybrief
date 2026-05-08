"""Tests for the Claude summarizer.

All Claude calls are mocked by the conftest autouse fixture.
No real API calls are made.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from dailybrief.models import Item, Summary
from dailybrief.pipeline.summarizer import _fallback_summary, summarize_batch


def _make_item(n: int, body: str = "Test body content.") -> Item:
    return Item(
        id=f"hash{n:03d}",
        title=f"Test Item {n}",
        url=f"https://example.com/{n}",
        body=body,
        category="RBI",
        source_id="test",
        priority=2,
        published_at=datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc),
        content_hash=f"hash{n:03d}",
    )


# ---------------------------------------------------------------------------
# skip_claude path (GUARDRAIL 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_claude_returns_rss_fallback(mock_anthropic):
    items = [_make_item(i, body="Some news content here.") for i in range(3)]
    summaries = await summarize_batch(items, api_key="fake", skip_claude=True)

    assert len(summaries) == 3
    assert all(isinstance(s, Summary) for s in summaries)
    # No real API call
    mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_skip_claude_empty_items(mock_anthropic):
    result = await summarize_batch([], api_key="fake", skip_claude=True)
    assert result == []
    mock_anthropic.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path: Claude returns valid JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_parses_json(mock_anthropic):
    items = [_make_item(i) for i in range(3)]
    payload = json.dumps([
        {"summary": f"Summary {i}.", "category": "RBI", "relevance_score": 7}
        for i in range(3)
    ])
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=payload)]
    mock_anthropic.messages.create.return_value.usage = MagicMock(input_tokens=100, output_tokens=60)

    summaries = await summarize_batch(items, api_key="fake", skip_claude=False)

    assert len(summaries) == 3
    assert summaries[0].summary == "Summary 0."
    assert summaries[0].relevance_score == 7
    assert summaries[0].category == "RBI"


@pytest.mark.asyncio
async def test_batch_split_into_groups_of_5(mock_anthropic):
    items = [_make_item(i) for i in range(7)]

    def _make_response_for(n):
        resp = MagicMock()
        resp.content = [MagicMock(text=json.dumps([
            {"summary": f"S{i}", "category": "Other", "relevance_score": 5}
            for i in range(n)
        ]))]
        resp.usage = MagicMock(input_tokens=50, output_tokens=20)
        return resp

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        n = len(json.loads(kwargs["messages"][0]["content"].split("ITEMS:\n")[-1]))
        call_count += 1
        return _make_response_for(n)

    mock_anthropic.messages.create.side_effect = side_effect

    summaries = await summarize_batch(items, api_key="fake", skip_claude=False)

    assert len(summaries) == 7
    # 7 items → 2 batches (5 + 2)
    assert call_count == 2


# ---------------------------------------------------------------------------
# Fallback path: malformed JSON → retry → fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_json_falls_back_after_two_attempts(mock_anthropic):
    items = [_make_item(1, body="Fallback body content here.")]
    mock_anthropic.messages.create.return_value.content = [MagicMock(text="not valid json")]
    mock_anthropic.messages.create.return_value.usage = MagicMock(input_tokens=10, output_tokens=5)

    summaries = await summarize_batch(items, api_key="fake", skip_claude=False)

    assert len(summaries) == 1
    # Should have tried twice, then fallen back to RSS
    assert mock_anthropic.messages.create.call_count == 2
    # Fallback summary contains part of the original body
    assert "Fallback body" in summaries[0].summary


@pytest.mark.asyncio
async def test_first_attempt_bad_second_good(mock_anthropic):
    """First attempt returns garbage; second attempt returns valid JSON."""
    items = [_make_item(1)]
    good_payload = json.dumps([{"summary": "Good summary.", "category": "RBI", "relevance_score": 8}])

    call_num = 0

    async def side_effect(**kwargs):
        nonlocal call_num
        call_num += 1
        resp = MagicMock()
        resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        resp.content = [MagicMock(text="GARBAGE" if call_num == 1 else good_payload)]
        return resp

    mock_anthropic.messages.create.side_effect = side_effect

    summaries = await summarize_batch(items, api_key="fake", skip_claude=False)

    assert summaries[0].summary == "Good summary."
    assert call_num == 2


# ---------------------------------------------------------------------------
# _fallback_summary helper
# ---------------------------------------------------------------------------

def test_fallback_summary_truncates():
    item = _make_item(1, body="A" * 300)
    s = _fallback_summary(item)
    assert len(s.summary) <= 202  # 200 chars + "…"
    assert s.summary.endswith("…")
    assert s.relevance_score == 5


def test_fallback_summary_short_body():
    item = _make_item(1, body="Short.")
    s = _fallback_summary(item)
    assert s.summary == "Short."
    assert not s.summary.endswith("…")


def test_fallback_summary_empty_body_uses_title():
    item = _make_item(1, body="")
    s = _fallback_summary(item)
    assert s.summary == item.title
