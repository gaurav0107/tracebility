"""Status-machine for backtest_draft / backtest_run lifecycles (Task 1,
D2-A). Pure-Python; no DB involved — the migrations that persist these
enums are checked separately below via a plain file-content assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langprobe_api.verbs.lifecycle import (
    TERMINAL,
    BacktestStatus,
    DraftStatus,
    can_transition,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# --- DraftStatus legal transitions ---------------------------------------


@pytest.mark.parametrize(
    "current,target",
    [
        (DraftStatus.DRAFTING, DraftStatus.BACKTESTING),
        (DraftStatus.DRAFTING, DraftStatus.DISCARDED),
        (DraftStatus.BACKTESTING, DraftStatus.READY),
        (DraftStatus.BACKTESTING, DraftStatus.DISCARDED),
        (DraftStatus.READY, DraftStatus.PROMOTED),
        (DraftStatus.READY, DraftStatus.BACKTESTING),
        (DraftStatus.READY, DraftStatus.DISCARDED),
    ],
)
def test_draft_legal_transitions(current, target):
    assert can_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        (DraftStatus.PROMOTED, DraftStatus.DRAFTING),
        (DraftStatus.PROMOTED, DraftStatus.READY),
        (DraftStatus.DISCARDED, DraftStatus.DRAFTING),
        (DraftStatus.DISCARDED, DraftStatus.READY),
        (DraftStatus.DRAFTING, DraftStatus.READY),
        (DraftStatus.DRAFTING, DraftStatus.PROMOTED),
        (DraftStatus.BACKTESTING, DraftStatus.PROMOTED),
    ],
)
def test_draft_illegal_transitions(current, target):
    assert can_transition(current, target) is False


def test_draft_terminal_statuses():
    assert DraftStatus.PROMOTED in TERMINAL
    assert DraftStatus.DISCARDED in TERMINAL
    assert DraftStatus.DRAFTING not in TERMINAL
    assert DraftStatus.BACKTESTING not in TERMINAL
    assert DraftStatus.READY not in TERMINAL


def test_no_transitions_out_of_terminal_draft_states():
    for terminal in (DraftStatus.PROMOTED, DraftStatus.DISCARDED):
        for target in DraftStatus:
            assert can_transition(terminal, target) is False


# --- BacktestStatus legal transitions -------------------------------------


@pytest.mark.parametrize(
    "current,target",
    [
        (BacktestStatus.QUEUED, BacktestStatus.RUNNING),
        (BacktestStatus.QUEUED, BacktestStatus.FAILED),
        (BacktestStatus.RUNNING, BacktestStatus.DONE),
        (BacktestStatus.RUNNING, BacktestStatus.FAILED),
    ],
)
def test_backtest_legal_transitions(current, target):
    assert can_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        (BacktestStatus.DONE, BacktestStatus.RUNNING),
        (BacktestStatus.FAILED, BacktestStatus.RUNNING),
        (BacktestStatus.QUEUED, BacktestStatus.DONE),
        (BacktestStatus.DONE, BacktestStatus.QUEUED),
    ],
)
def test_backtest_illegal_transitions(current, target):
    assert can_transition(current, target) is False


def test_backtest_terminal_statuses():
    assert BacktestStatus.DONE in TERMINAL
    assert BacktestStatus.FAILED in TERMINAL
    assert BacktestStatus.QUEUED not in TERMINAL
    assert BacktestStatus.RUNNING not in TERMINAL


def test_no_transitions_out_of_terminal_backtest_states():
    for terminal in (BacktestStatus.DONE, BacktestStatus.FAILED):
        for target in BacktestStatus:
            assert can_transition(terminal, target) is False


def test_same_state_transition_is_illegal():
    """No-op transitions aren't "legal moves" in the state machine —
    callers that want idempotent writes should short-circuit before
    calling can_transition, not rely on it returning True."""
    assert can_transition(DraftStatus.DRAFTING, DraftStatus.DRAFTING) is False
    assert can_transition(BacktestStatus.QUEUED, BacktestStatus.QUEUED) is False


def test_mismatched_enum_types_are_illegal():
    """A DraftStatus can never transition to a BacktestStatus and vice
    versa — the two machines are independent."""
    assert can_transition(DraftStatus.DRAFTING, BacktestStatus.QUEUED) is False
    assert can_transition(BacktestStatus.QUEUED, DraftStatus.DRAFTING) is False


# --- Migration file content checks (no live DB) ---------------------------


def test_postgres_backtest_migration_exists_and_defines_tables():
    path = REPO_ROOT / "schemas" / "postgres" / "migrations" / "0029_backtest.sql"
    assert path.exists(), f"expected migration at {path}"
    sql = path.read_text().lower()
    assert "create table if not exists backtest_draft" in sql
    assert "create table if not exists backtest_run" in sql
    assert "begin;" in sql
    assert "commit;" in sql


def test_clickhouse_backtest_migration_exists_and_defines_table():
    path = REPO_ROOT / "schemas" / "clickhouse" / "0009_backtest_score.sql"
    assert path.exists(), f"expected migration at {path}"
    sql = path.read_text().lower()
    assert "create table if not exists backtest_score" in sql
    assert "draft_id" in sql
    assert "replacingmergetree(judged_at)" in sql
