"""Recurring tick advances the watermark, is a no-op with no new runs,
and is single-writer per judge under concurrency (real Postgres).

The judge rows and the watermark writes go through a real local Postgres
(LANGPROBE_TEST_DSN); ClickHouse is faked so the test needs no CH and no
LLM provider. The real ``resolve_judge`` reads the seeded judge; only the
scoring dispatch (``apply_luna_judge``) is stubbed via ``_apply``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from langprobe_scheduler.ticks.recurring import evaluate_recurring_once

pytestmark = pytest.mark.asyncio


class _FakeClickHouse:
    """Serves runs newer than the watermark param; records inserts."""

    def __init__(self, runs: list[dict]) -> None:
        self._runs = runs
        self.inserts: list[list[tuple]] = []

    async def query(self, sql: str, parameters=None) -> list[dict]:
        wm = parameters["watermark"]
        fresh = [r for r in self._runs if r["start_time"] > wm]
        fresh.sort(key=lambda r: r["start_time"])
        return fresh[: parameters["limit"]]

    async def insert(self, table: str, rows, column_names) -> None:
        assert table == "eval_score"
        self.inserts.append(list(rows))


async def _fake_apply(judge_cfg, **kwargs) -> tuple[float, str, str, str, float]:
    return 0.2, "fail", "stub rationale", "score: 0.2", 0.0


async def _insert_project(pool: asyncpg.Pool):
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


async def _insert_recurring_judge(pool: asyncpg.Pool, *, scored_through, schedule_seconds=3600):
    project_id = await _insert_project(pool)
    suffix = uuid4().hex[:8]
    judge_id = await pool.fetchval(
        """
        insert into luna_judge
            (project_id, slug, name, rubric_prompt, provider, model,
             is_recurring, schedule_seconds, recurring_enabled, scored_through, last_scored_at)
        values ($1, $2, 'test', 'score it', 'stub', 'stub-model',
                true, $3, true, $4, null)
        returning id
        """,
        project_id,
        f"j-{suffix}",
        schedule_seconds,
        scored_through,
    )
    return judge_id, project_id


def _run(start_time: datetime) -> dict:
    return {
        "run_id": uuid4(),
        "start_time": start_time,
        "inputs": '{"q": "hi"}',
        "outputs": '{"a": "yo"}',
    }


async def test_watermark_advances_to_newest_run(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        t1 = t0 + timedelta(minutes=10)
        t2 = t0 + timedelta(minutes=20)
        judge_id, _ = await _insert_recurring_judge(pool, scored_through=t0)
        ch = _FakeClickHouse([_run(t1), _run(t2)])

        scored = await evaluate_recurring_once(pool, ch, max_cohort=500, _apply=_fake_apply)

        assert scored == 1
        assert len(ch.inserts) == 1
        assert len(ch.inserts[0]) == 2  # both runs scored
        row = await pool.fetchrow(
            "select scored_through, last_scored_at from luna_judge where id = $1", judge_id
        )
        assert row["scored_through"] == t2  # advanced to newest run
        assert row["last_scored_at"] is not None
    finally:
        await pool.close()


async def test_no_new_runs_is_a_noop(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        judge_id, _ = await _insert_recurring_judge(pool, scored_through=t0)
        # Only an old run, at/behind the watermark → nothing to score.
        ch = _FakeClickHouse([_run(t0 - timedelta(minutes=5))])

        scored = await evaluate_recurring_once(pool, ch, max_cohort=500, _apply=_fake_apply)

        assert scored == 0
        assert ch.inserts == []  # nothing written
        row = await pool.fetchrow(
            "select scored_through, last_scored_at from luna_judge where id = $1", judge_id
        )
        assert row["scored_through"] == t0  # watermark unmoved
        assert row["last_scored_at"] is not None  # but marked seen
    finally:
        await pool.close()


async def test_cost_cap_scores_a_prefix_then_resumes(integration_dsn: str) -> None:
    """A cap that only affords 2 of 5 runs scores the oldest 2, advances
    scored_through to the 2nd run, and the next tick finishes the rest."""

    async def _priced_apply(judge_cfg, **kwargs) -> tuple[float, str, str, str, float]:
        return 1.0, "pass", "", "", 0.10

    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        runs = [_run(t0 + timedelta(minutes=i)) for i in range(1, 6)]
        judge_id, _ = await _insert_recurring_judge(pool, scored_through=t0)
        ch = _FakeClickHouse(runs)

        scored = await evaluate_recurring_once(
            pool, ch, max_cohort=500, cost_cap_usd=0.25, _apply=_priced_apply
        )

        assert scored == 1
        assert len(ch.inserts) == 1
        assert len(ch.inserts[0]) == 2  # 2*0.10 <= 0.25 < 3*0.10
        row = await pool.fetchrow("select scored_through from luna_judge where id = $1", judge_id)
        assert row["scored_through"] == runs[1]["start_time"]  # watermark at 2nd scored run

        # Next tick: simulate the cadence elapsing (real time won't in this
        # test) so the judge is due again; only the un-scored runs are still
        # "new" (past the watermark).
        await pool.execute("update luna_judge set last_scored_at = null where id = $1", judge_id)
        ch2 = _FakeClickHouse(runs)
        scored2 = await evaluate_recurring_once(
            pool, ch2, max_cohort=500, cost_cap_usd=0.25, _apply=_priced_apply
        )

        assert scored2 == 1
        assert len(ch2.inserts) == 1
        assert len(ch2.inserts[0]) == 2  # capped again over the remaining 3 runs
    finally:
        await pool.close()


async def test_concurrent_ticks_score_judge_exactly_once(integration_dsn: str) -> None:
    pool = await asyncpg.create_pool(integration_dsn, min_size=4, max_size=8)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        t1 = t0 + timedelta(minutes=10)
        _, _ = await _insert_recurring_judge(pool, scored_through=t0)
        ch1 = _FakeClickHouse([_run(t1)])
        ch2 = _FakeClickHouse([_run(t1)])
        # Two replicas ticking the same due judge at once.
        await asyncio.gather(
            evaluate_recurring_once(pool, ch1, max_cohort=500, _apply=_fake_apply),
            evaluate_recurring_once(pool, ch2, max_cohort=500, _apply=_fake_apply),
        )
        total_inserts = len(ch1.inserts) + len(ch2.inserts)
        assert total_inserts == 1  # advisory lock + due re-check → scored once
    finally:
        await pool.close()
