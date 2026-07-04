"""Status machine for the agent-native eval-loop's draft/backtest
lifecycle (D2-A, Task 1).

Two independent state machines:

- ``DraftStatus`` — the lifecycle of a ``backtest_draft`` row (an
  AI-proposed judge, not yet promoted to a recurring eval).
- ``BacktestStatus`` — the lifecycle of a single ``backtest_run`` row
  (one SCRATCH backtest execution against history for a draft).

This module is pure Python: no DB access, no I/O. Callers (verbs /
routers, in later tasks) are expected to load the current status from
Postgres, call :func:`can_transition`, and only issue the UPDATE if it
returns ``True``.
"""

from __future__ import annotations

from enum import StrEnum


class DraftStatus(StrEnum):
    """Lifecycle of a ``backtest_draft`` row."""

    DRAFTING = "drafting"
    BACKTESTING = "backtesting"
    READY = "ready"
    PROMOTED = "promoted"
    DISCARDED = "discarded"


class BacktestStatus(StrEnum):
    """Lifecycle of a ``backtest_run`` row."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# Terminal states across both machines: once reached, no further
# transition is legal (a new draft / backtest_run must be created
# instead of mutating the old one).
TERMINAL: frozenset[DraftStatus | BacktestStatus] = frozenset(
    {
        DraftStatus.PROMOTED,
        DraftStatus.DISCARDED,
        BacktestStatus.DONE,
        BacktestStatus.FAILED,
    }
)

# Legal forward edges. A draft can be sent back for another backtest
# round from READY (e.g. the operator tweaks judge_config and wants a
# fresh cohort run) but never regresses to DRAFTING once backtesting
# has started, and never leaves a terminal state.
_DRAFT_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {
    DraftStatus.DRAFTING: frozenset({DraftStatus.BACKTESTING, DraftStatus.DISCARDED}),
    DraftStatus.BACKTESTING: frozenset({DraftStatus.READY, DraftStatus.DISCARDED}),
    DraftStatus.READY: frozenset(
        {DraftStatus.PROMOTED, DraftStatus.BACKTESTING, DraftStatus.DISCARDED}
    ),
    DraftStatus.PROMOTED: frozenset(),
    DraftStatus.DISCARDED: frozenset(),
}

_BACKTEST_TRANSITIONS: dict[BacktestStatus, frozenset[BacktestStatus]] = {
    BacktestStatus.QUEUED: frozenset({BacktestStatus.RUNNING, BacktestStatus.FAILED}),
    BacktestStatus.RUNNING: frozenset({BacktestStatus.DONE, BacktestStatus.FAILED}),
    BacktestStatus.DONE: frozenset(),
    BacktestStatus.FAILED: frozenset(),
}

_TRANSITIONS: dict[type, dict] = {
    DraftStatus: _DRAFT_TRANSITIONS,
    BacktestStatus: _BACKTEST_TRANSITIONS,
}


def can_transition(
    current: DraftStatus | BacktestStatus, target: DraftStatus | BacktestStatus
) -> bool:
    """Return True iff ``target`` is a legal next state from ``current``.

    Both statuses must belong to the same machine (``DraftStatus`` or
    ``BacktestStatus``) — the two machines never interoperate. Self
    transitions (current == target) are always illegal; callers that
    want idempotent no-op writes should check equality before calling
    this function.
    """
    if type(current) is not type(target):
        return False
    transitions = _TRANSITIONS.get(type(current))
    if transitions is None:
        return False
    return target in transitions.get(current, frozenset())
