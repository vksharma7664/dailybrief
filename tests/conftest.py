"""Global test fixtures.

GUARDRAIL 3: auto-mock the anthropic client for every test.
It is impossible to make a real API call from any test unless the mock
is explicitly bypassed — which no test should do.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Anthropic mock — applied to EVERY test automatically
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_anthropic(monkeypatch):
    """Replace anthropic.AsyncAnthropic with a mock that returns a canned response.

    Tests that need to inspect mock interactions can declare this fixture as a
    parameter and use the returned mock_client object.

    The default response is an empty JSON array ("[]") which is a valid
    summarizer response for zero items.  Individual tests override
    mock_client.messages.create.return_value as needed.
    """
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[]")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    monkeypatch.setattr("anthropic.AsyncAnthropic", MagicMock(return_value=mock_client))

    return mock_client


# ---------------------------------------------------------------------------
# Cost-tracker reset — prevent state bleed between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cost_tracker(tmp_path):
    from dailybrief.pipeline.cost_tracker import cost_tracker
    # Redirect JSONL writes to a temp dir so project logs/ stays clean during tests
    os.environ["API_CALLS_LOG"] = str(tmp_path / "api_calls.jsonl")
    cost_tracker.reset()
    # Clear env overrides so each test gets the default caps
    for var in ("MAX_RUN_COST_INR", "MAX_CLAUDE_CALLS_PER_RUN"):
        os.environ.pop(var, None)
    yield
    cost_tracker.reset()
    os.environ.pop("API_CALLS_LOG", None)
