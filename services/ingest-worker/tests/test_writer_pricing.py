"""Ingest-side pricing backfill.

When an SDK/OTel exporter reports token counts but no cost, the worker
backfills ``cost_usd`` from the model price table (same litellm source the
API gateway uses) so downstream cost queries aren't blank. Unknown models
leave cost at 0 rather than raising.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from langprobe_worker.writer import (
    _RUN_COLUMNS,
    _SPAN_COLUMNS,
    _row_for_run,
    _row_for_span,
)


def _envelope(*, project_id: str) -> dict:
    return {
        "project_id": project_id,
        "org_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "received_at": "2026-06-07T00:00:00Z",
        "payload": {"runs": []},
    }


def test_llm_span_tokens_only_backfills_cost() -> None:
    """A known-model llm span with tokens but no cost gets a positive
    cost_usd computed from the model price table."""
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "name": "llm-1",
        "kind": "llm",
        "status": "ok",
        "start_time": "2026-06-07T00:00:00Z",
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        # no cost_usd
    }
    row = _row_for_span(env, span, parent_run_id=str(uuid4()))
    cost = row[_SPAN_COLUMNS.index("cost_usd")]
    # gpt-4o-mini: 1000 prompt + 500 completion == 0.00015 + 0.0003 == 0.00045
    assert cost > 0
    assert abs(float(cost) - 0.00045) < 1e-9


def test_llm_span_cost_precision_is_decimal() -> None:
    """Backfilled cost is a Decimal quantized to the ClickHouse Decimal(18,8)
    column scale (no float drift into the wire)."""
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "kind": "llm",
        "start_time": "2026-06-07T00:00:00Z",
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }
    row = _row_for_span(env, span, parent_run_id=str(uuid4()))
    cost = row[_SPAN_COLUMNS.index("cost_usd")]
    assert isinstance(cost, Decimal)
    # 8 decimal places, matches Decimal(18, 8)
    assert -cost.as_tuple().exponent <= 8


def test_unknown_model_leaves_cost_zero() -> None:
    """An unknown model must not raise; cost stays 0."""
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "kind": "llm",
        "start_time": "2026-06-07T00:00:00Z",
        "model": "totally-unknown-model-xyz-999",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }
    row = _row_for_span(env, span, parent_run_id=str(uuid4()))
    cost = row[_SPAN_COLUMNS.index("cost_usd")]
    assert cost == 0


def test_existing_cost_not_overwritten() -> None:
    """A span that already reports a cost keeps it untouched."""
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "kind": "llm",
        "start_time": "2026-06-07T00:00:00Z",
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cost_usd": 0.99,
    }
    row = _row_for_span(env, span, parent_run_id=str(uuid4()))
    cost = row[_SPAN_COLUMNS.index("cost_usd")]
    assert float(cost) == 0.99


def test_non_llm_span_no_backfill() -> None:
    """Only llm spans get token-priced; a tool span stays at 0 even with
    tokens present."""
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "kind": "tool",
        "start_time": "2026-06-07T00:00:00Z",
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }
    row = _row_for_span(env, span, parent_run_id=str(uuid4()))
    cost = row[_SPAN_COLUMNS.index("cost_usd")]
    assert cost == 0


def test_run_cost_rolls_up_from_spans() -> None:
    """When the run reports 0 cost, it rolls up the sum of its spans'
    (backfilled) costs, mirroring the token rollup pattern."""
    env = _envelope(project_id=str(uuid4()))
    run = {
        "run_id": str(uuid4()),
        "kind": "chain",
        "start_time": "2026-06-07T00:00:00Z",
        "spans": [
            {
                "span_id": str(uuid4()),
                "kind": "llm",
                "start_time": "2026-06-07T00:00:00Z",
                "model": "gpt-4o-mini",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            },
            {
                "span_id": str(uuid4()),
                "kind": "llm",
                "start_time": "2026-06-07T00:00:00Z",
                "model": "gpt-4o-mini",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            },
        ],
    }
    row = _row_for_run(env, run)
    cost = row[_RUN_COLUMNS.index("cost_usd")]
    # two spans of 0.00045 each
    assert abs(float(cost) - 0.0009) < 1e-9


def test_run_cost_not_rolled_up_when_reported() -> None:
    """A run that already reports its own cost keeps it."""
    env = _envelope(project_id=str(uuid4()))
    run = {
        "run_id": str(uuid4()),
        "kind": "llm",
        "start_time": "2026-06-07T00:00:00Z",
        "cost_usd": 5.0,
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "spans": [],
    }
    row = _row_for_run(env, run)
    cost = row[_RUN_COLUMNS.index("cost_usd")]
    assert float(cost) == 5.0
