"""Alert tick fires once and is single-writer under concurrency (real Postgres)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from langprobe_scheduler.ticks.alerts import evaluate_alerts_once

pytestmark = pytest.mark.asyncio


class _FakeClickHouse:
    """Returns a fixed error_rate so a '> 0.0' rule always breaches."""

    async def query(self, sql: str, parameters=None) -> list[dict]:
        return [{"runs": 10, "errors": 5}]


async def _insert_project(pool: asyncpg.Pool) -> object:
    """Seed the org -> workspace -> project FK chain alert_rule requires."""
    suffix = uuid4().hex[:12]
    org_id = await pool.fetchval(
        "insert into org (slug, name) values ($1, $2) returning id",
        f"org-{suffix}",
        "test org",
    )
    workspace_id = await pool.fetchval(
        "insert into workspace (org_id, slug, name) values ($1, $2, $3) returning id",
        org_id,
        f"ws-{suffix}",
        "test workspace",
    )
    return await pool.fetchval(
        "insert into project (workspace_id, slug, name) values ($1, $2, $3) returning id",
        workspace_id,
        f"proj-{suffix}",
        "test project",
    )


async def _insert_rule(pool: asyncpg.Pool) -> object:
    project_id = await _insert_project(pool)
    return await pool.fetchval(
        """
        insert into alert_rule
            (project_id, name, metric, comparator, threshold, window_seconds, enabled)
        values ($1, 'test', 'error_rate', '>', 0.0, 300, true)
        returning id
        """,
        project_id,
    )


async def test_alert_tick_opens_one_incident(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        rule_id = await _insert_rule(pool)
        await evaluate_alerts_once(pool, _FakeClickHouse())
        events = await pool.fetchval(
            "select count(*) from alert_event where rule_id = $1 and kind = 'fired'", rule_id
        )
        assert events == 1
    finally:
        await pool.close()


async def test_concurrent_ticks_open_exactly_one_incident(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=4, max_size=8)
    try:
        rule_id = await _insert_rule(pool)
        # Two replicas ticking at once over the same due rule.
        await asyncio.gather(
            evaluate_alerts_once(pool, _FakeClickHouse()),
            evaluate_alerts_once(pool, _FakeClickHouse()),
        )
        fired = await pool.fetchval(
            "select count(*) from alert_event where rule_id = $1 and kind = 'fired'", rule_id
        )
        assert fired == 1  # advisory lock prevents double-fire
    finally:
        await pool.close()
