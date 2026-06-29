"""Replay executor orchestration (Phase 0 span-level what-if).

`execute_replay` decides which spans re-execute and assembles the
original/replayed span pairs that feed `compute_replay_diff`. The actual LLM
dispatch is INJECTED so this orchestration is unit-testable without the live
gateway / Postgres / ClickHouse.

Phase 0 contract: only the edited span(s) re-dispatch live; every other span is
held at its captured value. An edited span with no capture, or whose dispatch
fails, is surfaced loudly (paired with None) — never silently skipped.
"""

from __future__ import annotations

from langprobe_api.replay.diff import compute_replay_diff
from langprobe_api.replay.executor import (
    DispatchOutcome,
    ReplayEdit,
    execute_replay,
)


def _span(span_id, *, kind="llm", model="anthropic/claude-sonnet-4-6",
          outputs="orig", cost_usd=0.001, latency_ms=100):
    return {
        "span_id": span_id,
        "name": kind,
        "kind": kind,
        "model": model,
        "outputs": outputs,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
    }


def _fake_dispatch(outcome: DispatchOutcome):
    calls = []

    async def dispatch(span, edits):
        calls.append((span["span_id"], [e.field for e in edits]))
        return outcome

    dispatch.calls = calls  # type: ignore[attr-defined]
    return dispatch


async def test_edited_span_redispatched_others_held():
    spans = [_span("a", outputs="old"), _span("b", outputs="keep")]
    edits = [ReplayEdit(target_span_id="a", field="prompt", value="new prompt")]
    dispatch = _fake_dispatch(
        DispatchOutcome(outputs="new", model="anthropic/claude-sonnet-4-6",
                        cost_usd=0.002, latency_ms=140,
                        prompt_tokens=10, completion_tokens=5)
    )
    plan = await execute_replay(
        spans, edits, dispatch=dispatch, capturable_span_ids={"a", "b"}
    )
    # only the edited span dispatched
    assert dispatch.calls == [("a", ["prompt"])]
    assert plan.edited_span_ids == {"a"}
    assert plan.missing_capture_span_ids == set()

    diff = compute_replay_diff(
        plan.pairs,
        edited_span_ids=plan.edited_span_ids,
        missing_capture_span_ids=plan.missing_capture_span_ids,
    )
    assert diff.span_count_total == 2
    assert diff.span_count_diverged == 1
    assert diff.outcome == "ok"  # edited divergence is expected
    delta_a = next(d for d in diff.deltas if d.span_id == "a")
    assert delta_a.replayed_output == "new"
    assert delta_a.original_output == "old"
    delta_b = next(d for d in diff.deltas if d.span_id == "b")
    assert delta_b.diverged is False


async def test_no_edits_holds_everything():
    spans = [_span("a"), _span("b")]
    dispatch = _fake_dispatch(DispatchOutcome("x", "m", 0, 0, 0, 0))
    plan = await execute_replay(
        spans, [], dispatch=dispatch, capturable_span_ids={"a", "b"}
    )
    assert dispatch.calls == []  # nothing re-dispatched
    diff = compute_replay_diff(plan.pairs, edited_span_ids=plan.edited_span_ids)
    assert diff.span_count_diverged == 0
    assert diff.determinism == "deterministic"


async def test_edited_span_without_capture_is_loud():
    spans = [_span("a")]
    edits = [ReplayEdit(target_span_id="a", field="prompt", value="x")]
    dispatch = _fake_dispatch(DispatchOutcome("y", "m", 0, 0, 0, 0))
    plan = await execute_replay(
        spans, edits, dispatch=dispatch, capturable_span_ids=set()
    )
    assert dispatch.calls == []  # never dispatched — no capture to anchor
    assert plan.missing_capture_span_ids == {"a"}
    diff = compute_replay_diff(
        plan.pairs,
        edited_span_ids=plan.edited_span_ids,
        missing_capture_span_ids=plan.missing_capture_span_ids,
    )
    assert diff.outcome == "tool_io_missing"


async def test_dispatch_failure_surfaces_not_silent():
    spans = [_span("a")]
    edits = [ReplayEdit(target_span_id="a", field="prompt", value="x")]

    async def failing_dispatch(span, edits):
        return DispatchOutcome("", "", 0, 0, 0, 0, error="provider 500")

    plan = await execute_replay(
        spans, edits, dispatch=failing_dispatch, capturable_span_ids={"a"}
    )
    # paired with None => diff escalates loudly
    pair = plan.pairs[0]
    assert pair[1] is None
    diff = compute_replay_diff(
        plan.pairs,
        edited_span_ids=plan.edited_span_ids,
        missing_capture_span_ids=plan.missing_capture_span_ids,
    )
    assert diff.span_count_diverged == 1
    assert diff.outcome == "tool_io_missing"


async def test_multiple_edits_same_span_passed_together():
    spans = [_span("a")]
    edits = [
        ReplayEdit(target_span_id="a", field="prompt", value="p"),
        ReplayEdit(target_span_id="a", field="temperature", value=0.2),
    ]
    dispatch = _fake_dispatch(DispatchOutcome("out", "m", 0.001, 50, 1, 1))
    plan = await execute_replay(
        spans, edits, dispatch=dispatch, capturable_span_ids={"a"}
    )
    assert dispatch.calls == [("a", ["prompt", "temperature"])]


# --- apply_llm_edits: pure edit application (extracted from the endpoint) ---
from langprobe_api.replay.executor import apply_llm_edits  # noqa: E402


def test_apply_edits_prompt_model_temperature():
    prompt, model, temp, applied, skipped = apply_llm_edits(
        base_inputs="orig prompt",
        base_model="anthropic/claude-sonnet-4-6",
        base_temperature=0.0,
        edits=[
            ReplayEdit("a", "prompt", "edited"),
            ReplayEdit("a", "model", "anthropic/claude-opus-4-8"),
            ReplayEdit("a", "temperature", 0.7),
        ],
    )
    assert prompt == "edited"
    assert model == "anthropic/claude-opus-4-8"
    assert temp == 0.7
    assert set(applied) == {"prompt", "model", "temperature"}
    assert skipped == []


def test_apply_edits_defaults_when_no_edits():
    prompt, model, temp, applied, skipped = apply_llm_edits(
        base_inputs="keep", base_model="m", base_temperature=0.3, edits=[]
    )
    assert (prompt, model, temp) == ("keep", "m", 0.3)
    assert applied == []


def test_apply_edits_invalid_temperature_skipped():
    _, _, temp, applied, skipped = apply_llm_edits(
        base_inputs="p", base_model="m", base_temperature=0.1,
        edits=[ReplayEdit("a", "temperature", "not-a-number")],
    )
    assert temp == 0.1  # unchanged
    assert "temperature" not in applied
    assert any("temperature" in s for s in skipped)


def test_apply_edits_tool_args_metadata_only():
    _, _, _, applied, _ = apply_llm_edits(
        base_inputs="p", base_model="m", base_temperature=0.0,
        edits=[ReplayEdit("a", "tool_args", {"x": 1})],
    )
    assert any("tool_args" in a for a in applied)
