"""replay_run record builder + agent-legible diff summary.

`build_replay_run_row` maps a ReplayDiff onto the columns schema 0003 already
defined for the `replay_run` ClickHouse table — column order must match exactly
or the insert silently mis-binds.

`summarize_diff` produces the token-budgeted, LLM-legible one-liner that the MCP
surface (Phase 1) hands an agent: what diverged and where, not a raw span dump.
"""

from __future__ import annotations

from uuid import UUID

from langprobe_api.replay.diff import ReplayDiff, SpanDelta
from langprobe_api.replay.record import (
    REPLAY_RUN_COLUMNS,
    build_replay_run_row,
    summarize_diff,
)

_PROJ = UUID("11111111-1111-1111-1111-111111111111")
_RID = UUID("22222222-2222-2222-2222-222222222222")
_ORIG = UUID("33333333-3333-3333-3333-333333333333")


def _diff(**kw):
    base = {
        "span_count_total": 3,
        "span_count_diverged": 1,
        "determinism": "deterministic",
        "outcome": "ok",
        "deltas": [],
    }
    base.update(kw)
    return ReplayDiff(**base)


def test_row_matches_schema_column_order_and_arity():
    diff = _diff()
    row = build_replay_run_row(
        diff,
        project_id=_PROJ,
        replay_run_id=_RID,
        original_run_id=_ORIG,
        started_at="2026-06-29T00:00:00Z",
        finished_at="2026-06-29T00:00:01Z",
    )
    assert len(row) == len(REPLAY_RUN_COLUMNS)
    by = dict(zip(REPLAY_RUN_COLUMNS, row, strict=True))
    assert by["project_id"] == str(_PROJ)
    assert by["replay_run_id"] == str(_RID)
    assert by["original_run_id"] == str(_ORIG)
    assert by["determinism"] == "deterministic"
    assert by["outcome"] == "ok"
    assert by["span_count_total"] == 3
    assert by["span_count_diverged"] == 1
    assert by["schema_version"] == 1


def test_notes_carry_model_drift_for_er18():
    diff = _diff(
        determinism="env_drift",
        outcome="model_version_diff",
        deltas=[
            SpanDelta(
                span_id="a", name="llm", diverged=True, output_changed=True,
                model_changed=True, original_output="x", replayed_output="y",
                cost_delta_usd=0.0, latency_delta_ms=0,
                note="model m1 -> m2 (ER-18)",
            )
        ],
    )
    row = build_replay_run_row(
        diff, project_id=_PROJ, replay_run_id=_RID, original_run_id=_ORIG,
        started_at="2026-06-29T00:00:00Z", finished_at=None,
    )
    notes = dict(zip(REPLAY_RUN_COLUMNS, row, strict=True))["notes"]
    assert "ER-18" in notes or "m1 -> m2" in notes


def test_finished_at_nullable():
    row = build_replay_run_row(
        _diff(), project_id=_PROJ, replay_run_id=_RID, original_run_id=_ORIG,
        started_at="2026-06-29T00:00:00Z", finished_at=None,
    )
    assert dict(zip(REPLAY_RUN_COLUMNS, row, strict=True))["finished_at"] is None


def test_summary_is_concise_and_localizes_divergence():
    diff = _diff(
        outcome="ok", span_count_total=5, span_count_diverged=1,
        deltas=[
            SpanDelta(span_id="span-abc", name="llm", diverged=True,
                      output_changed=True, model_changed=False,
                      original_output="a", replayed_output="b",
                      cost_delta_usd=0.002, latency_delta_ms=30, note=""),
        ],
    )
    s = summarize_diff(diff)
    assert "1/5" in s  # diverged/total
    assert "span-abc" in s  # localizes where
    assert len(s) < 280  # token-budgeted one-liner


def test_summary_flags_missing_capture_loud():
    diff = _diff(
        determinism="tool_unavailable", outcome="tool_io_missing",
        span_count_total=2, span_count_diverged=1,
        deltas=[
            SpanDelta(span_id="s1", name="tool", diverged=True,
                      output_changed=False, model_changed=False,
                      original_output="", replayed_output="",
                      cost_delta_usd=0.0, latency_delta_ms=0,
                      note="capture missing — not replayable"),
        ],
    )
    s = summarize_diff(diff)
    assert "tool_io_missing" in s
    assert "not fully replayable" in s.lower() or "capture missing" in s.lower()
