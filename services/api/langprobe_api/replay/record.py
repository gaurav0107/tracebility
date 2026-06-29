"""replay_run record builder + agent-legible diff summary.

``build_replay_run_row`` maps a ``ReplayDiff`` onto the ``replay_run`` ClickHouse
table (schema 0003). Column order is pinned in ``REPLAY_RUN_COLUMNS`` and must
match the insert exactly.

``summarize_diff`` is the token-budgeted, LLM-legible one-liner the MCP surface
(Phase 1) hands an agent: what diverged and where. An agent reading this at 2am
should learn the verdict and the divergence location without paging through a
raw span dump.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .diff import ReplayDiff

# Pinned to schemas/clickhouse/0003_replay_captures.sql `replay_run`.
REPLAY_RUN_COLUMNS: tuple[str, ...] = (
    "project_id",
    "replay_run_id",
    "original_run_id",
    "determinism",
    "span_count_total",
    "span_count_diverged",
    "outcome",
    "notes",
    "started_at",
    "finished_at",
    "schema_version",
)


def _notes(diff: ReplayDiff) -> str:
    """Compact human/agent notes — divergent spans with their reason."""
    bits: list[str] = []
    for d in diff.deltas:
        if d.diverged and d.note:
            bits.append(f"{d.span_id}: {d.note}")
    return "; ".join(bits)


def build_replay_run_row(
    diff: ReplayDiff,
    *,
    project_id: UUID,
    replay_run_id: UUID,
    original_run_id: UUID,
    started_at: Any,
    finished_at: Any,
) -> tuple[Any, ...]:
    """Build the ``replay_run`` row tuple (order = ``REPLAY_RUN_COLUMNS``)."""
    return (
        str(project_id),
        str(replay_run_id),
        str(original_run_id),
        diff.determinism,
        diff.span_count_total,
        diff.span_count_diverged,
        diff.outcome,
        _notes(diff),
        started_at,
        finished_at,
        1,
    )


def summarize_diff(diff: ReplayDiff) -> str:
    """One-line, token-budgeted verdict for agent consumption.

    Shape: ``replay <outcome>: N/M spans diverged [at <span>...]<hint>``
    """
    diverged = [d for d in diff.deltas if d.diverged]
    where = ", ".join(d.span_id for d in diverged[:3])
    if len(diverged) > 3:
        where += f", +{len(diverged) - 3} more"

    head = (
        f"replay {diff.outcome}: "
        f"{diff.span_count_diverged}/{diff.span_count_total} spans diverged"
    )
    if where:
        head += f" at {where}"

    if diff.outcome == "tool_io_missing":
        head += " — not fully replayable (capture missing)"
    elif diff.outcome == "model_version_diff":
        head += " — model endpoint changed (ER-18)"
    elif diff.outcome == "replay_nondeterministic":
        head += " — non-edited span drifted"

    return head
