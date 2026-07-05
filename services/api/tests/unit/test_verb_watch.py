"""watch_judge verb (Task 6, D2-A).

An agent driving a backtest run polls ``watch_judge`` for status. The
one behavior that matters beyond a plain status read: a ``running``
run whose ``heartbeat_at`` has gone stale (older than
``LEASE_TIMEOUT_S``) is declared ``failed`` right here, in-band, on the
next poll — otherwise a GKE pod restart that orphans the executor
would leave the poller waiting forever for a run that will never
update again.

Everything here mocks ``deps.pool`` (asyncpg) — no real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import WatchIn
from langprobe_api.verbs.scope import ScopeError
from langprobe_api.verbs.watch import LEASE_TIMEOUT_S, watch_judge
from langprobe_tenant.context import TenantContext


def _make_ctx(project_id) -> TenantContext:
    return TenantContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        project_id=project_id,
        api_key_id=uuid4(),
        plan="pro",
        scopes=frozenset({"verbs:*"}),
    )


def _run_row(
    run_id,
    project_id,
    *,
    status="running",
    heartbeat_at=None,
    caught=None,
    missed=None,
    error=None,
):
    return {
        "id": run_id,
        "draft_id": uuid4(),
        "project_id": project_id,
        "status": status,
        "cohort_size": 3,
        "spans_scanned": 3,
        "cost_usd": 0.01,
        "caught": caught,
        "missed": missed,
        "would_have_flagged_at": None,
        "item_total": 3,
        "item_done": 3,
        "heartbeat_at": heartbeat_at,
        "started_at": datetime.now(UTC),
        "finished_at": None,
        "error": error,
    }


def _make_pool(mocker, *, fetchrow_rows=None, fetchrow_side_effect=None):
    pool = mocker.MagicMock(name="pool")
    if fetchrow_side_effect is not None:
        pool.fetchrow = mocker.AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = mocker.AsyncMock(side_effect=fetchrow_rows or [])
    pool.execute = mocker.AsyncMock(return_value="UPDATE 1")
    return pool


def _make_deps(pool) -> VerbDeps:
    return VerbDeps(pool=pool, ch=None)


# ----- stale heartbeat termination -----------------------------------------


async def test_watch_running_stale_heartbeat_marks_failed_and_updates(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    run_id = uuid4()
    stale = datetime.now(UTC) - timedelta(seconds=LEASE_TIMEOUT_S + 30)
    run = _run_row(run_id, project_id, status="running", heartbeat_at=stale)

    pool = _make_pool(mocker, fetchrow_rows=[run])
    deps = _make_deps(pool)

    out = await watch_judge(deps, ctx, WatchIn(target_id=run_id))

    assert out.status == "failed"
    assert out.error == "heartbeat_timeout"

    update_call = next(
        c for c in pool.execute.await_args_list if "update backtest_run" in c.args[0]
    )
    assert "failed" in str(update_call.args)
    assert "heartbeat_timeout" in str(update_call.args)


async def test_watch_running_fresh_heartbeat_returns_running_no_update(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    run_id = uuid4()
    fresh = datetime.now(UTC) - timedelta(seconds=5)
    run = _run_row(run_id, project_id, status="running", heartbeat_at=fresh)

    pool = _make_pool(mocker, fetchrow_rows=[run])
    deps = _make_deps(pool)

    out = await watch_judge(deps, ctx, WatchIn(target_id=run_id))

    assert out.status == "running"
    pool.execute.assert_not_awaited()


# ----- terminal states pass through -----------------------------------------


async def test_watch_done_returns_caught_missed(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    run_id = uuid4()
    run = _run_row(run_id, project_id, status="done", caught=2, missed=1)

    pool = _make_pool(mocker, fetchrow_rows=[run])
    deps = _make_deps(pool)

    out = await watch_judge(deps, ctx, WatchIn(target_id=run_id))

    assert out.status == "done"
    assert out.caught == 2
    assert out.missed == 1
    pool.execute.assert_not_awaited()


async def test_watch_failed_returns_existing_error_no_update(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    run_id = uuid4()
    run = _run_row(run_id, project_id, status="failed", error="cap_exceeded:cost_usd")

    pool = _make_pool(mocker, fetchrow_rows=[run])
    deps = _make_deps(pool)

    out = await watch_judge(deps, ctx, WatchIn(target_id=run_id))

    assert out.status == "failed"
    assert out.error == "cap_exceeded:cost_usd"
    pool.execute.assert_not_awaited()


# ----- scope ----------------------------------------------------------------


async def test_watch_other_project_run_raises_scope_error(mocker):
    ctx = _make_ctx(uuid4())
    run = _run_row(uuid4(), project_id=uuid4(), status="running")  # different project

    pool = _make_pool(mocker, fetchrow_rows=[run])
    deps = _make_deps(pool)

    with pytest.raises(ScopeError):
        await watch_judge(deps, ctx, WatchIn(target_id=run["id"]))

    pool.execute.assert_not_awaited()


async def test_watch_missing_run_raises_scope_error(mocker):
    ctx = _make_ctx(uuid4())
    pool = _make_pool(mocker, fetchrow_rows=[None])
    deps = _make_deps(pool)

    with pytest.raises(ScopeError):
        await watch_judge(deps, ctx, WatchIn(target_id=uuid4()))
