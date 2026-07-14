"""promote auto-provisions one bound judge_score_avg watch rule, idempotently."""

from __future__ import annotations

import pytest
from langprobe_api.verbs.promote import _provision_watch_rule

pytestmark = pytest.mark.asyncio


async def test_provision_uses_bound_defaults_and_clamps_window(mocker):
    pool = mocker.MagicMock()
    pool.execute = mocker.AsyncMock(return_value="INSERT 0 1")
    judge_id = "44444444-4444-4444-4444-444444444444"
    project_id = "11111111-1111-1111-1111-111111111111"

    await _provision_watch_rule(
        pool,
        project_id=project_id,
        judge_id=judge_id,
        slug="proposed-abc",
        schedule_seconds=30,  # below the 900s floor
        created_by=None,
    )

    sql, *args = pool.execute.call_args.args
    assert "judge_score_avg" in sql
    assert "on conflict" in sql.lower()
    assert 900 in args  # window clamped up to the 900s floor
    assert "<" in args  # comparator
    assert 0.5 in args  # threshold
    assert judge_id in args  # subject binding

    # Mid case: 3x cadence, no clamping.
    pool.execute.reset_mock()
    await _provision_watch_rule(
        pool,
        project_id=project_id,
        judge_id=judge_id,
        slug="proposed-abc",
        schedule_seconds=3600,
        created_by=None,
    )
    _, *args = pool.execute.call_args.args
    assert 10800 in args  # 3x cadence

    # Upper clamp: 3x cadence exceeds the 86400s ceiling.
    pool.execute.reset_mock()
    await _provision_watch_rule(
        pool,
        project_id=project_id,
        judge_id=judge_id,
        slug="proposed-abc",
        schedule_seconds=40000,
        created_by=None,
    )
    _, *args = pool.execute.call_args.args
    assert 86400 in args  # window clamped down to the 86400s ceiling
