"""Replay diff computation (Phase 0 of the replay engine).

`compute_replay_diff` is the payoff of the replay wedge: given the original
run's spans correlated with the replayed run's spans, it produces the per-span
delta plus the `replay_run` summary fields (determinism / outcome /
span_count_diverged) that schema 0003 already defined.

It is a PURE function. Span correlation (the 1C content-hash match key) is a
separate concern handled upstream — this function consumes already-paired
spans, so it works identically for Phase 0 (span-id pairing) and Phase 2
(content-hash pairing).

Pairing model: each entry is (original | None, replayed | None).
  (orig, repl)  -> both ran; compare.
  (orig, None)  -> span did not re-execute (capture missing / skipped).
  (None, repl)  -> new span appeared because control flow diverged.

A span dict carries: span_id, name, model, outputs (str), cost_usd (float),
latency_ms (int).
"""

from __future__ import annotations

from langprobe_api.replay.diff import compute_replay_diff


def _span(span_id, *, name="llm", model="anthropic/claude-sonnet-4-6",
          outputs="hello", cost_usd=0.001, latency_ms=100):
    return {
        "span_id": span_id,
        "name": name,
        "model": model,
        "outputs": outputs,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
    }


def test_identical_replay_is_deterministic_zero_divergence():
    """Replay with no edit and identical output => zero divergence, ok."""
    pairs = [(_span("a"), _span("a")), (_span("b"), _span("b"))]
    diff = compute_replay_diff(pairs, edited_span_ids=set())
    assert diff.span_count_total == 2
    assert diff.span_count_diverged == 0
    assert diff.determinism == "deterministic"
    assert diff.outcome == "ok"
    assert all(not d.diverged for d in diff.deltas)


def test_edited_span_divergence_is_expected_still_deterministic():
    """The edited span changing output is the intended signal, not a
    determinism failure. Non-edited spans unchanged => deterministic."""
    pairs = [
        (_span("a", outputs="old"), _span("a", outputs="new")),
        (_span("b"), _span("b")),
    ]
    diff = compute_replay_diff(pairs, edited_span_ids={"a"})
    assert diff.span_count_diverged == 1
    assert diff.determinism == "deterministic"
    assert diff.outcome == "ok"
    delta_a = next(d for d in diff.deltas if d.span_id == "a")
    assert delta_a.diverged is True
    assert delta_a.output_changed is True
    assert delta_a.original_output == "old"
    assert delta_a.replayed_output == "new"


def test_non_edited_span_drift_is_nondeterministic():
    """A non-edited span whose output changed despite same input => the
    replay is nondeterministic (temp>0 sampling, flaky tool, etc.)."""
    pairs = [(_span("a", outputs="x"), _span("a", outputs="y"))]
    diff = compute_replay_diff(pairs, edited_span_ids=set())
    assert diff.span_count_diverged == 1
    assert diff.determinism == "nondeterministic"
    assert diff.outcome == "replay_nondeterministic"


def test_model_change_on_span_flags_model_version_diff():
    """ER-18: a model endpoint diff is warned via outcome, not silent."""
    pairs = [
        (_span("a", model="anthropic/claude-sonnet-4-6"),
         _span("a", model="anthropic/claude-opus-4-8", outputs="z")),
    ]
    diff = compute_replay_diff(pairs, edited_span_ids=set())
    assert diff.outcome == "model_version_diff"
    assert diff.determinism == "env_drift"
    delta = diff.deltas[0]
    assert delta.model_changed is True
    assert "claude-sonnet-4-6" in delta.note
    assert "claude-opus-4-8" in delta.note


def test_missing_capture_degrades_loud_not_silent():
    """A span that could not be re-executed (capture missing) must surface
    as tool_io_missing, never a silent wrong diff."""
    pairs = [(_span("a"), None)]
    diff = compute_replay_diff(
        pairs, edited_span_ids=set(), missing_capture_span_ids={"a"}
    )
    assert diff.outcome == "tool_io_missing"
    assert diff.determinism == "tool_unavailable"
    delta = diff.deltas[0]
    assert delta.diverged is True
    assert "capture missing" in delta.note.lower()


def test_new_span_from_divergence_is_marked():
    """Control-flow divergence (Phase 2): a span exists on replay that had
    no original. It counts as diverged and is labelled."""
    pairs = [(_span("a"), _span("a")), (None, _span("c"))]
    diff = compute_replay_diff(pairs, edited_span_ids=set())
    assert diff.span_count_diverged == 1
    delta_c = next(d for d in diff.deltas if d.span_id == "c")
    assert delta_c.diverged is True
    assert "new span" in delta_c.note.lower()


def test_cost_and_latency_deltas_computed():
    pairs = [(
        _span("a", cost_usd=0.001, latency_ms=100),
        _span("a", cost_usd=0.004, latency_ms=160, outputs="diff"),
    )]
    diff = compute_replay_diff(pairs, edited_span_ids={"a"})
    delta = diff.deltas[0]
    assert abs(delta.cost_delta_usd - 0.003) < 1e-9
    assert delta.latency_delta_ms == 60


def test_outcome_precedence_model_diff_beats_nondeterminism():
    """When multiple signals fire, model_version_diff is the headline."""
    pairs = [
        (_span("a", outputs="x"), _span("a", outputs="y")),  # nondeterministic
        (_span("b", model="m1"), _span("b", model="m2", outputs="z")),  # model diff
    ]
    diff = compute_replay_diff(pairs, edited_span_ids=set())
    assert diff.outcome == "model_version_diff"
