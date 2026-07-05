"""Reranker spans produce a replay_capture row.

Batch 1 added the `reranker` span kind. A reranker sits on the
retrieval boundary — its output (the re-ordered docs) can drift as the
index/model changes — so it's replay-relevant and maps to the
``retrieval`` capture kind. guardrail/evaluator/workflow/task stay OUT
(no clear deterministic IO boundary; deferred per the design).
"""

from __future__ import annotations

from uuid import uuid4

from langprobe_worker.writer import (
    _REPLAY_CAPTURE_COLUMNS,
    _row_for_replay_capture,
)


def _envelope(*, project_id: str) -> dict:
    return {
        "project_id": project_id,
        "org_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "received_at": "2026-06-07T00:00:00Z",
        "payload": {"runs": []},
    }


def test_reranker_span_yields_retrieval_capture() -> None:
    env = _envelope(project_id=str(uuid4()))
    span = {
        "span_id": str(uuid4()),
        "name": "rerank-1",
        "kind": "reranker",
        "status": "ok",
        "start_time": "2026-06-07T00:00:00Z",
        "inputs": '{"query": "hi"}',
        "outputs": '{"docs": ["a", "b"]}',
    }
    row = _row_for_replay_capture(env, span, parent_run_id=str(uuid4()))
    assert row is not None
    kind = row[_REPLAY_CAPTURE_COLUMNS.index("kind")]
    assert kind == "retrieval"


def test_reranker_capture_hashes_query_and_docs() -> None:
    """Retrieval payload is inputs + outputs; same bytes -> same hash."""
    env = _envelope(project_id=str(uuid4()))
    base = {
        "span_id": str(uuid4()),
        "kind": "reranker",
        "inputs": '{"query": "hi"}',
        "outputs": '{"docs": ["a", "b"]}',
    }
    row_a = _row_for_replay_capture(env, base, parent_run_id=str(uuid4()))
    row_b = _row_for_replay_capture(
        env, {**base, "span_id": str(uuid4())}, parent_run_id=str(uuid4())
    )
    assert row_a is not None and row_b is not None
    h = _REPLAY_CAPTURE_COLUMNS.index("content_hash")
    assert row_a[h] == row_b[h]


def test_deferred_kinds_produce_no_capture() -> None:
    """guardrail/evaluator/workflow/task have no deterministic IO
    boundary yet and must not emit a replay_capture row."""
    env = _envelope(project_id=str(uuid4()))
    for kind in ("guardrail", "evaluator", "workflow", "task"):
        span = {
            "span_id": str(uuid4()),
            "kind": kind,
            "inputs": "{}",
            "outputs": "{}",
        }
        row = _row_for_replay_capture(env, span, parent_run_id=str(uuid4()))
        assert row is None, f"{kind} should not be replay-relevant"
