"""watch_judge still flips a stale running run to failed, now with a
status-guarded UPDATE."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import WatchIn
from langprobe_api.verbs.watch import watch_judge

pytestmark = pytest.mark.asyncio


async def test_watch_flips_stale_running_with_status_guard(mocker):
    project_id = "11111111-1111-1111-1111-111111111111"
    stale = datetime.now(UTC) - timedelta(seconds=9999)
    pool = mocker.MagicMock(name="pool")
    pool.fetchrow = mocker.AsyncMock(
        return_value={
            "id": "22222222-2222-2222-2222-222222222222",
            "status": "running",
            "caught": 0,
            "missed": 0,
            "error": None,
            "heartbeat_at": stale,
            "project_id": project_id,
        }
    )
    pool.execute = mocker.AsyncMock(return_value="UPDATE 1")
    deps = VerbDeps(pool=pool, ch=mocker.MagicMock())
    ctx = mocker.MagicMock(project_ids=[project_id])

    mocker.patch("langprobe_api.verbs.watch.require_project_scope", return_value=None)

    out = await watch_judge(deps, ctx, WatchIn(target_id="22222222-2222-2222-2222-222222222222"))

    assert out.status == "failed"
    assert out.error == "heartbeat_timeout"
    # the UPDATE must carry the status guard
    sql = pool.execute.call_args.args[0]
    assert "status = 'running'" in sql
