"""Replay executor — decides what re-executes and assembles the diff input.

Phase 0 (span-level what-if): only the edited span(s) re-dispatch live; every
other span is held at its captured value. The result is a ``ReplayPlan`` whose
``pairs`` feed ``compute_replay_diff`` directly.

The LLM dispatch is injected (a ``Dispatch`` callable) so this orchestration is
unit-testable without the gateway. The endpoint supplies a real dispatch that
calls ``langprobe_api.llm.dispatch`` (same path Studio uses); tests supply a
fake.

Loudness invariant: an edited span that cannot be replayed — no capture, or a
dispatch error — is paired with ``None`` so the diff escalates to
``tool_io_missing``. It is never silently dropped (ER-23).

Phase 2 will replace this single-span dispatcher with the SDK harness that
re-executes real control flow and serves non-edited calls from capture via the
1C content-hash match. The diff contract (``pairs``) stays identical.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# Spans whose I/O the replayer can anchor on. Orchestration spans
# (chain/agent/parser/embedding) are not re-dispatched in Phase 0.
_REPLAYABLE_KINDS = frozenset({"llm", "tool", "retriever"})


@dataclass(frozen=True)
class ReplayEdit:
    target_span_id: str
    field: str  # "prompt" | "model" | "temperature" | "tool_args"
    value: Any


@dataclass(frozen=True)
class DispatchOutcome:
    outputs: str
    model: str
    cost_usd: float
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None


@dataclass(frozen=True)
class ReplayPlan:
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]]
    edited_span_ids: set[str]
    missing_capture_span_ids: set[str]


Dispatch = Callable[[dict[str, Any], list[ReplayEdit]], Awaitable[DispatchOutcome]]


def apply_llm_edits(
    *,
    base_inputs: str,
    base_model: str,
    base_temperature: float | None,
    edits: list[ReplayEdit],
) -> tuple[str, str, float | None, list[str], list[str]]:
    """Apply edits to an LLM span's prompt/model/temperature.

    Pure. Mirrors Studio's edit semantics so the endpoint stays thin and the
    risky bit stays tested. Returns
    ``(prompt, model, temperature, applied, skipped)``. ``tool_args`` is
    recorded as metadata-only in Phase 0 (tools don't re-dispatch via the LLM
    gateway). Invalid values are skipped loudly, never silently coerced.
    """
    prompt = base_inputs
    model = base_model
    temperature = base_temperature
    applied: list[str] = []
    skipped: list[str] = []
    for edit in edits:
        if edit.field == "prompt":
            prompt = str(edit.value or "")
            applied.append("prompt")
        elif edit.field == "model":
            model = str(edit.value or "")
            applied.append("model")
        elif edit.field == "temperature":
            try:
                temperature = float(edit.value)
                applied.append("temperature")
            except (TypeError, ValueError):
                skipped.append("temperature(invalid)")
        elif edit.field == "tool_args":
            applied.append("tool_args(metadata-only)")
        else:
            skipped.append(f"{edit.field}(unknown)")
    return prompt, model, temperature, applied, skipped


async def execute_replay(
    original_spans: list[dict[str, Any]],
    edits: list[ReplayEdit],
    *,
    dispatch: Dispatch,
    capturable_span_ids: set[str],
) -> ReplayPlan:
    """Run the Phase 0 span-level replay and assemble diff pairs.

    ``capturable_span_ids`` are spans with a ``replay_capture`` row — only those
    can be anchored for replay. ``dispatch`` re-executes one edited span given
    its edits.
    """
    edits_by_span: dict[str, list[ReplayEdit]] = {}
    for e in edits:
        edits_by_span.setdefault(e.target_span_id, []).append(e)
    edited_span_ids = set(edits_by_span)

    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    missing: set[str] = set()

    for span in original_spans:
        sid = str(span.get("span_id") or "")
        if sid not in edited_span_ids:
            # Held at captured value: replayed == original.
            pairs.append((span, dict(span)))
            continue

        # Edited span: must be replayable to re-dispatch.
        if sid not in capturable_span_ids:
            missing.add(sid)
            pairs.append((span, None))
            continue

        outcome = await dispatch(span, edits_by_span[sid])
        if outcome.error:
            # Loud failure — paired with None, diff escalates.
            pairs.append((span, None))
            continue

        replayed = dict(span)
        replayed["outputs"] = outcome.outputs
        replayed["model"] = outcome.model or span.get("model") or ""
        replayed["cost_usd"] = outcome.cost_usd
        replayed["latency_ms"] = outcome.latency_ms
        replayed["prompt_tokens"] = outcome.prompt_tokens
        replayed["completion_tokens"] = outcome.completion_tokens
        pairs.append((span, replayed))

    return ReplayPlan(
        pairs=pairs,
        edited_span_ids=edited_span_ids,
        missing_capture_span_ids=missing,
    )
