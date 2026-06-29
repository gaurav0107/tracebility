"""Replay diff computation — the payoff of the replay wedge.

``compute_replay_diff`` takes the original run's spans correlated with the
replayed run's spans and produces the per-span delta plus the ``replay_run``
summary fields that schema 0003 already defined (determinism / outcome /
span_count_diverged).

PURE function. Span correlation (the 1C content-hash match key) is handled
upstream, so this works identically for Phase 0 (span-id pairing) and Phase 2
(content-hash pairing).

Pairing model — each entry is ``(original | None, replayed | None)``::

    (orig, repl)  both ran            -> compare outputs / model / cost / latency
    (orig, None)  did not re-execute  -> capture missing / skipped (loud)
    (None, repl)  appeared on replay  -> control-flow divergence (Phase 2)

Severity precedence (most severe wins; determinism + outcome move together):

    tool_unavailable   / tool_io_missing          replay integrity compromised
    env_drift          / model_version_diff        ER-18: model endpoint changed
    nondeterministic   / replay_nondeterministic   non-edited span drifted
    deterministic      / ok                         only edited spans changed

An edited span changing its output/model is the *intended* signal, not a
determinism failure — divergence on a span in ``edited_span_ids`` never
escalates determinism.

    ORIGINAL run spans          REPLAYED run spans
    ┌────────────┐  pair        ┌────────────┐
    │ span a ────┼─────────────▶│ span a'    │  output/model/cost/latency Δ
    │ span b ────┼─────────────▶│ span b'    │
    │ span c ────┼──────╳ None  │            │  capture missing -> loud
    │            │  None ╳──────┼─ span d    │  new span -> divergence
    └────────────┘              └────────────┘
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Determinism enum (schema 0003 replay_run.determinism)
DET_DETERMINISTIC = "deterministic"
DET_NONDETERMINISTIC = "nondeterministic"
DET_ENV_DRIFT = "env_drift"
DET_TOOL_UNAVAILABLE = "tool_unavailable"

# Outcome enum (schema 0003 replay_run.outcome)
OUT_OK = "ok"
OUT_NONDETERMINISTIC = "replay_nondeterministic"
OUT_TOOL_IO_MISSING = "tool_io_missing"
OUT_MODEL_VERSION_DIFF = "model_version_diff"

# Severity tiers, most severe first. Each tier maps to (determinism, outcome).
# Tier 0 is the most severe; ``ok`` is the floor.
_TIER_MISSING = 3
_TIER_MODEL = 2
_TIER_NONDET = 1
_TIER_OK = 0

_TIER_TO_VERDICT = {
    _TIER_MISSING: (DET_TOOL_UNAVAILABLE, OUT_TOOL_IO_MISSING),
    _TIER_MODEL: (DET_ENV_DRIFT, OUT_MODEL_VERSION_DIFF),
    _TIER_NONDET: (DET_NONDETERMINISTIC, OUT_NONDETERMINISTIC),
    _TIER_OK: (DET_DETERMINISTIC, OUT_OK),
}


@dataclass(frozen=True)
class SpanDelta:
    span_id: str
    name: str
    diverged: bool
    output_changed: bool
    model_changed: bool
    original_output: str
    replayed_output: str
    cost_delta_usd: float
    latency_delta_ms: int
    note: str = ""


@dataclass(frozen=True)
class ReplayDiff:
    span_count_total: int
    span_count_diverged: int
    determinism: str
    outcome: str
    deltas: list[SpanDelta] = field(default_factory=list)


def _s(span: dict[str, Any] | None, key: str, default: Any) -> Any:
    if span is None:
        return default
    val = span.get(key)
    return default if val is None else val


def compute_replay_diff(
    pairs: Sequence[tuple[dict[str, Any] | None, dict[str, Any] | None]],
    *,
    edited_span_ids: Iterable[str],
    missing_capture_span_ids: Iterable[str] = (),
) -> ReplayDiff:
    """Diff correlated original/replayed span pairs into a ``ReplayDiff``.

    ``edited_span_ids``: spans the user intentionally changed — their
    divergence is expected and never escalates determinism.
    ``missing_capture_span_ids``: spans that could not be re-executed because
    their capture is absent — surfaced loudly as ``tool_io_missing``.
    """
    edited = set(edited_span_ids)
    missing = set(missing_capture_span_ids)

    deltas: list[SpanDelta] = []
    diverged_count = 0
    worst_tier = _TIER_OK

    for original, replayed in pairs:
        span_id = str(_s(replayed, "span_id", None) or _s(original, "span_id", ""))
        name = str(_s(replayed, "name", None) or _s(original, "name", ""))
        is_edited = span_id in edited

        orig_out = str(_s(original, "outputs", ""))
        repl_out = str(_s(replayed, "outputs", ""))
        orig_cost = float(_s(original, "cost_usd", 0.0))
        repl_cost = float(_s(replayed, "cost_usd", 0.0))
        orig_lat = int(_s(original, "latency_ms", 0))
        repl_lat = int(_s(replayed, "latency_ms", 0))
        orig_model = str(_s(original, "model", ""))
        repl_model = str(_s(replayed, "model", ""))

        note = ""
        output_changed = False
        model_changed = False

        if original is None and replayed is not None:
            # Control-flow divergence produced a span with no original.
            diverged = True
            note = "new span (divergence)"
            if not is_edited:
                worst_tier = max(worst_tier, _TIER_NONDET)
        elif replayed is None:
            # Span did not re-execute. Loud, never silent.
            diverged = True
            if span_id in missing:
                note = "capture missing — not replayable"
            else:
                note = "span not re-executed"
            worst_tier = max(worst_tier, _TIER_MISSING)
        else:
            output_changed = orig_out != repl_out
            model_changed = orig_model != repl_model
            diverged = output_changed or model_changed
            if model_changed:
                note = f"model {orig_model} -> {repl_model} (ER-18)"
                if not is_edited:
                    worst_tier = max(worst_tier, _TIER_MODEL)
            if output_changed and not model_changed and not is_edited:
                worst_tier = max(worst_tier, _TIER_NONDET)

        if diverged:
            diverged_count += 1

        deltas.append(
            SpanDelta(
                span_id=span_id,
                name=name,
                diverged=diverged,
                output_changed=output_changed,
                model_changed=model_changed,
                original_output=orig_out,
                replayed_output=repl_out,
                cost_delta_usd=repl_cost - orig_cost,
                latency_delta_ms=repl_lat - orig_lat,
                note=note,
            )
        )

    determinism, outcome = _TIER_TO_VERDICT[worst_tier]
    return ReplayDiff(
        span_count_total=len(pairs),
        span_count_diverged=diverged_count,
        determinism=determinism,
        outcome=outcome,
        deltas=deltas,
    )
