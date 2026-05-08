"""
Cost tracking, call-count cap, and retry-aware Claude call wrapper.

All Claude API calls must go through call_claude() so that:
  - Tokens are counted and INR cost accumulated.
  - Per-run cost and call-count caps are enforced (raises CostCapExceeded).
  - Transient errors are retried once (2s backoff); non-transient 4xx re-raised immediately.
  - Every call is written to logs/api_calls.jsonl as an audit trail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)

# Pricing: claude-sonnet-4-6  (USD per million tokens)
INPUT_USD_PER_MTK: float = 3.00
OUTPUT_USD_PER_MTK: float = 15.00
USD_TO_INR: float = 84.0

ITEMS_PER_BATCH: int = 5
# Token budget estimates (conservative upper bounds per call type)
_BATCH_IN_TOKENS: int = 700
_BATCH_OUT_TOKENS: int = 300
_WHY_IN_TOKENS: int = 200
_WHY_OUT_TOKENS: int = 60


class CostCapExceeded(Exception):
    """Raised when accumulated cost or call count exceeds the configured cap."""


class _CostTracker:
    """Singleton that accumulates token usage across one pipeline run."""

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.call_count: int = 0

    @property
    def _log_path(self) -> Path:
        """Read from env so tests can redirect writes without touching the project log."""
        return Path(os.getenv("API_CALLS_LOG", "logs/api_calls.jsonl"))

    # ------------------------------------------------------------------
    # Reset (call at the start of every run; also used in tests)
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.call_count = 0

    # ------------------------------------------------------------------
    # Config properties (read env at call time so tests can override)
    # ------------------------------------------------------------------

    @property
    def max_cost_inr(self) -> float:
        return float(os.getenv("MAX_RUN_COST_INR", "50"))

    @property
    def max_calls(self) -> int:
        return int(os.getenv("MAX_CLAUDE_CALLS_PER_RUN", "15"))

    # ------------------------------------------------------------------
    # Cost arithmetic
    # ------------------------------------------------------------------

    def _to_inr(self, inp: int, out: int) -> float:
        usd = (inp * INPUT_USD_PER_MTK + out * OUTPUT_USD_PER_MTK) / 1_000_000
        return usd * USD_TO_INR

    def estimate_cost_inr(self) -> float:
        return self._to_inr(self.input_tokens, self.output_tokens)

    # ------------------------------------------------------------------
    # Record a completed call
    # ------------------------------------------------------------------

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
        context: str = "",
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.call_count += 1
        call_cost = self._to_inr(input_tokens, output_tokens)
        logger.info(
            "Claude call #%d: in=%d out=%d ₹%.4f (cumulative ₹%.4f) [%s]",
            self.call_count,
            input_tokens,
            output_tokens,
            call_cost,
            self.estimate_cost_inr(),
            context,
        )
        self._write_jsonl(input_tokens, output_tokens, call_cost, model, context)

    # ------------------------------------------------------------------
    # Cap enforcement — call after every add()
    # ------------------------------------------------------------------

    def check_cap(self) -> None:
        if self.call_count > self.max_calls:
            raise CostCapExceeded(
                f"Call count {self.call_count} exceeds cap {self.max_calls}"
            )
        cost = self.estimate_cost_inr()
        if cost > self.max_cost_inr:
            raise CostCapExceeded(
                f"Accumulated cost ₹{cost:.2f} exceeds cap ₹{self.max_cost_inr:.2f}"
            )

    # ------------------------------------------------------------------
    # Pre-flight estimate (before any calls are made)
    # ------------------------------------------------------------------

    def preflight(self, n_items: int) -> tuple[int, float]:
        """Return (total_call_count, max_cost_inr) for *n_items* items."""
        n_batches = math.ceil(n_items / ITEMS_PER_BATCH)
        n_why = min(3, n_items)
        est_in = n_batches * _BATCH_IN_TOKENS + n_why * _WHY_IN_TOKENS
        est_out = n_batches * _BATCH_OUT_TOKENS + n_why * _WHY_OUT_TOKENS
        return n_batches + n_why, self._to_inr(est_in, est_out)

    # ------------------------------------------------------------------
    # JSONL audit log
    # ------------------------------------------------------------------

    def _write_jsonl(
        self,
        inp: int,
        out: int,
        cost_inr: float,
        model: str,
        context: str,
    ) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "call_num": self.call_count,
                "input_tokens": inp,
                "output_tokens": out,
                "cost_inr": round(cost_inr, 6),
                "cumulative_cost_inr": round(self.estimate_cost_inr(), 6),
                "model": model,
                "context": context,
            }
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed to write API call log: %s", exc)


# Module-level singleton — import this everywhere
cost_tracker = _CostTracker()


# ---------------------------------------------------------------------------
# Retry-aware Claude call wrapper
# ---------------------------------------------------------------------------

async def call_claude(
    client: Any,
    context: str = "",
    **kwargs: Any,
) -> Any:
    """
    Wrap client.messages.create() with:
      - 1 retry on transient errors (2 s backoff); no retry on 4xx.
      - Token tracking via cost_tracker.add().
      - Cap check via cost_tracker.check_cap() — raises CostCapExceeded.
    """
    import anthropic as _ant  # local import: keeps module importable before install

    TRANSIENT = (
        _ant.RateLimitError,
        _ant.APITimeoutError,
        _ant.APIConnectionError,
    )

    last_exc: Exception | None = None
    # delays[i] = sleep BEFORE attempt i (None = no sleep)
    delays = [None, 2.0]

    for attempt, pre_sleep in enumerate(delays):
        if pre_sleep is not None:
            await asyncio.sleep(pre_sleep)
        try:
            response = await client.messages.create(**kwargs)
            cost_tracker.add(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=kwargs.get("model", ""),
                context=context,
            )
            cost_tracker.check_cap()
            return response
        except CostCapExceeded:
            raise  # never retry a cap hit
        except TRANSIENT as exc:
            last_exc = exc
            logger.warning("Transient error attempt %d: %s", attempt + 1, exc)
        except _ant.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                logger.warning(
                    "Server error %d attempt %d: %s",
                    exc.status_code,
                    attempt + 1,
                    exc,
                )
            else:
                raise  # 4xx = auth/bad-request/content-policy — don't waste retries
        except Exception:
            raise  # unexpected error — surface immediately

    assert last_exc is not None
    raise last_exc
