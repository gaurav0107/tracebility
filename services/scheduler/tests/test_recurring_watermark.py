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
        self.column_names: list[str] | None = None

    async def query(self, sql: str, parameters=None) -> list[dict]:
        wm = parameters["watermark"]
        wm_run_id = parameters.get("watermark_run_id")
        if wm_run_id is not None:
            # Composite (start_time, run_id) cursor: filter and order by the
            # same tuple, mirroring the real `(start_time, run_id) > (...)` SQL.
            def key(r):
                return (r["start_time"], str(r["run_id"]))

            cursor = (wm, str(wm_run_id))
            fresh = [r for r in self._runs if key(r) > cursor]
            fresh.sort(key=key)
        else:
            # Legacy time-only cursor (pre-fix code path).
            fresh = [r for r in self._runs if r["start_time"] > wm]
            fresh.sort(key=lambda r: r["start_time"])
        return fresh[: parameters["limit"]]

    async def insert(self, table: str, rows, column_names) -> None:
        assert table == "eval_score"
        self.column_names = list(column_names)
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
    """A cap that only affords 2 of 5 runs still records the tipping (3rd)
    run — append-then-check overshoots by at most one score's cost rather
    than discarding the paid-for call — and advances scored_through to
    that 3rd run; the next tick finishes the rest."""

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
        assert len(ch.inserts[0]) == 3  # 2*0.10 <= 0.25, tipping 3rd run still recorded
        row = await pool.fetchrow("select scored_through from luna_judge where id = $1", judge_id)
        assert row["scored_through"] == runs[2]["start_time"]  # watermark at 3rd (tipping) run

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
        assert len(ch2.inserts[0]) == 2  # remaining 2 runs, 2*0.10 <= 0.25, never exhausted
    finally:
        await pool.close()


async def test_eval_score_rows_carry_tenant_columns(integration_dsn: str) -> None:
    """Regression: recurring eval_score inserts must lead with the
    (org_id, workspace_id) tuple so scores land under the judge's real tenant,
    not the zero-UUID default. eval_score is keyed on org_id (0006) with no
    DEFAULT, so an insert that omits it silently poisons the tenant partition
    and hides the scores from every tenant-scoped read. Mirrors the manual
    eval path (routers/evals.py); see tenant_scope.resolve_tenant_ids."""
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        t1 = t0 + timedelta(minutes=10)
        judge_id, project_id = await _insert_recurring_judge(pool, scored_through=t0)
        tenant = await pool.fetchrow(
            """
            select workspace.org_id as org_id, project.workspace_id as workspace_id
              from project
              join workspace on workspace.id = project.workspace_id
             where project.id = $1
            """,
            project_id,
        )
        ch = _FakeClickHouse([_run(t1)])

        scored = await evaluate_recurring_once(pool, ch, max_cohort=500, _apply=_fake_apply)

        assert scored == 1
        # column order + row values both matter: the insert positions must line up.
        assert ch.column_names is not None
        assert ch.column_names[:3] == ["org_id", "workspace_id", "project_id"]
        row = ch.inserts[0][0]
        zero_uuid = "00000000-0000-0000-0000-000000000000"
        assert row[0] == str(tenant["org_id"]) != zero_uuid  # org_id, not the default
        assert row[1] == str(tenant["workspace_id"]) != zero_uuid  # workspace_id
        assert row[2] == str(project_id)
    finally:
        await pool.close()


async def test_start_time_tie_at_cohort_boundary_is_not_dropped(integration_dsn: str) -> None:
    """Regression (issue #1): two runs sharing an identical start_time must
    both be scored even when the max_cohort cap splits the tie group across
    ticks. A pure `start_time > watermark` cursor advances to the boundary
    timestamp and the strict `>` then skips the tie survivor forever; the
    composite (start_time, run_id) cursor drains it on the next tick instead.
    """
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        t0 = datetime.now(UTC) - timedelta(hours=1)
        t1 = t0 + timedelta(minutes=10)
        t2 = t0 + timedelta(minutes=20)
        # A@t1, then B and C sharing t2 (distinct run_ids) — the tie.
        a = _run(t1)
        b = _run(t2)
        c = _run(t2)
        all_ids = {str(a["run_id"]), str(b["run_id"]), str(c["run_id"])}
        judge_id, _ = await _insert_recurring_judge(pool, scored_through=t0)

        scored_ids: set[str] = set()
        # cap=2 forces the t2 tie group to straddle the cohort boundary.
        ch1 = _FakeClickHouse([a, b, c])
        await evaluate_recurring_once(pool, ch1, max_cohort=2, _apply=_fake_apply)
        for batch in ch1.inserts:
            scored_ids.update(row[3] for row in batch)  # row[3] = run_id (see column order)

        # Make the judge due again and run a second tick over the same runs.
        await pool.execute("update luna_judge set last_scored_at = null where id = $1", judge_id)
        ch2 = _FakeClickHouse([a, b, c])
        await evaluate_recurring_once(pool, ch2, max_cohort=2, _apply=_fake_apply)
        for batch in ch2.inserts:
            scored_ids.update(row[3] for row in batch)

        assert scored_ids == all_ids  # every run scored — the tie survivor is not dropped
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
