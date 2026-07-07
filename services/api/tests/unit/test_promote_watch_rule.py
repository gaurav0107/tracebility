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
        schedule_seconds=30,  # below the 60 floor
        created_by=None,
    )

    sql, *args = pool.execute.call_args.args
    assert "judge_score_avg" in sql
    assert "on conflict" in sql.lower()
    assert 60 in args  # window clamped up to the 60s floor
    assert "<" in args  # comparator
    assert 0.5 in args  # threshold
    assert judge_id in args  # subject binding
