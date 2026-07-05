"""Backtest reaper behavior (real Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from langprobe_scheduler.ticks.reaper import reap_once

pytestmark = pytest.mark.asyncio


async def _insert_run(pool: asyncpg.Pool, *, status: str, heartbeat_at) -> str:
    draft_id = await pool.fetchval(
        """
        insert into backtest_draft (project_id, org_id, cluster_ref, judge_kind, judge_config)
        values ($1, $2, $3::jsonb, $4, $5::jsonb)
        returning id
        """,
        uuid4(),
        uuid4(),
        "{}",
        "luna",
        "{}",
    )
    return await pool.fetchval(
        """
        insert into backtest_run (draft_id, status, heartbeat_at)
        values ($1, $2, $3)
        returning id
        """,
        draft_id,
        status,
        heartbeat_at,
    )


async def test_reaps_stale_running(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        stale = datetime.now(UTC) - timedelta(seconds=600)
        run_id = await _insert_run(pool, status="running", heartbeat_at=stale)
        reaped = await reap_once(pool, lease_timeout_s=120)
        assert reaped >= 1
        row = await pool.fetchrow(
            "select status, error, finished_at from backtest_run where id = $1", run_id
        )
        assert row["status"] == "failed"
        assert row["error"] == "heartbeat_timeout"
        assert row["finished_at"] is not None
    finally:
        await pool.close()


async def test_reaps_running_with_null_heartbeat(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        run_id = await _insert_run(pool, status="running", heartbeat_at=None)
        await reap_once(pool, lease_timeout_s=120)
        status = await pool.fetchval("select status from backtest_run where id = $1", run_id)
        assert status == "failed"
    finally:
        await pool.close()


async def test_leaves_fresh_running_alone(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        fresh = datetime.now(UTC)
        run_id = await _insert_run(pool, status="running", heartbeat_at=fresh)
        await reap_once(pool, lease_timeout_s=120)
        status = await pool.fetchval("select status from backtest_run where id = $1", run_id)
        assert status == "running"
    finally:
        await pool.close()


async def test_ignores_terminal_runs(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        stale = datetime.now(UTC) - timedelta(seconds=600)
        run_id = await _insert_run(pool, status="done", heartbeat_at=stale)
        await reap_once(pool, lease_timeout_s=120)
        status = await pool.fetchval("select status from backtest_run where id = $1", run_id)
        assert status == "done"
    finally:
        await pool.close()
