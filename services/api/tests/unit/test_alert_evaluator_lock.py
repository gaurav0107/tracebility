"""Per-rule advisory lock makes _apply_rule_decision single-writer (real Postgres)."""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest
from langprobe_api.alerts.evaluator import _apply_rule_decision

pytestmark = pytest.mark.asyncio


def _dsn() -> str:
    dsn = os.environ.get("LANGPROBE_TEST_DSN")
    if not dsn:
        pytest.skip("set LANGPROBE_TEST_DSN to run integration tests")
    return dsn


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


async def _insert_rule(pool: asyncpg.Pool) -> asyncpg.Record:
    project_id = await _insert_project(pool)
    rule_id = await pool.fetchval(
        """
        insert into alert_rule
            (project_id, name, metric, comparator, threshold, window_seconds, enabled)
        values ($1, 'test', 'error_rate', '>', 0.0, 300, true)
        returning id
        """,
        project_id,
    )
    return await pool.fetchrow(
        """
        select id, project_id, metric, comparator, threshold,
               window_seconds, open_incident_id
          from alert_rule where id = $1
        """,
        rule_id,
    )


async def test_apply_rule_decision_skips_when_lock_held() -> None:
    pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=4)
    try:
        rule = await _insert_rule(pool)
        key = f"alert-rule:{rule['id']}"
        # Hold the same advisory lock on a separate session inside a live txn.
        blocker = await pool.acquire()
        tx = blocker.transaction()
        await tx.start()
        got = await blocker.fetchval(
            "select pg_try_advisory_xact_lock(hashtextextended($1, 0))", key
        )
        assert got is True
        try:
            # value=1.0 breaches threshold 0.0; without the lock this opens an incident.
            await _apply_rule_decision(pool, rule, 1.0)
            open_id = await pool.fetchval(
                "select open_incident_id from alert_rule where id = $1", rule["id"]
            )
            events = await pool.fetchval(
                "select count(*) from alert_event where rule_id = $1", rule["id"]
            )
            assert open_id is None  # lock held -> no incident opened
            assert events == 0  # lock held -> no event written
        finally:
            await tx.rollback()
            await pool.release(blocker)
    finally:
        await pool.close()


async def test_apply_rule_decision_opens_incident_when_unlocked() -> None:
    pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=4)
    try:
        rule = await _insert_rule(pool)
        await _apply_rule_decision(pool, rule, 1.0)  # breaches 0.0
        open_id = await pool.fetchval(
            "select open_incident_id from alert_rule where id = $1", rule["id"]
        )
        kind = await pool.fetchval("select kind from alert_event where rule_id = $1", rule["id"])
        assert open_id is not None
        assert kind == "fired"
    finally:
        await pool.close()
