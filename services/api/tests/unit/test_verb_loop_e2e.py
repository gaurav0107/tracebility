"""Full-loop composition test for the agent-native eval loop (Task 8,
D2-A).

Walks the VERB SERVICE LAYER end-to-end (not through HTTP or MCP):

    cluster_failures -> propose_eval -> run_judge_over_cohort
        -> _run_backtest -> promote_to_recurring -> watch_judge

using mocked ``deps.pool`` (asyncpg) + ``deps.ch`` (ClickHouse) + a
DETERMINISTIC judge (``contains``) + a mocked LLM proposer (patches
``langprobe_api.verbs.propose._draft_via_llm`` to return a valid
deterministic-judge rubric JSON, so no network call happens and the
draft's ``judge_kind``/``judge_config`` shape matches what
``_run_backtest``'s ``_score_run`` already knows how to score
deterministically — see ``verbs/backtest.py``'s ``_score_run``, which
scores ``contains`` by checking `expected in output`).

Three properties under test:
  1. The loop composes — each verb's output plugs directly into the
     next verb's input, with no manual reshaping beyond what the
     design already documents (e.g. pulling ``sample_run_ids`` out of a
     ``Cluster``, ``draft_id`` out of an ``EvalDraftOut``).
  2. The deterministic backtest yields a STABLE, reproducible
     caught/missed split — re-running the same executor call against
     the same fixture again produces the exact same numbers.
  3. A cross-project ``TenantContext`` is rejected (``ScopeError``) at
     EVERY verb in the loop, not just some of them.

Mocking follows the shapes established in each verb's own unit test
(``test_verb_cluster.py``, ``test_verb_propose.py``,
``test_verb_backtest.py``, ``test_verb_promote.py``,
``test_verb_watch.py``) — a single shared Postgres/ClickHouse double
is built per scenario below since the loop spans multiple verbs
against the same "backing store".
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.lifecycle import BacktestStatus, DraftStatus
from langprobe_api.verbs.models import (
    BacktestIn,
    ClusterFailuresIn,
    PromoteIn,
    ProposeEvalIn,
    WatchIn,
)
from langprobe_api.verbs.scope import ScopeError
from langprobe_api.verbs.service import (
    cluster_failures,
    promote_to_recurring,
    propose_eval,
    run_judge_over_cohort,
    watch_judge,
)
from langprobe_tenant.context import TenantContext

# The deterministic-judge rubric the mocked LLM proposer returns. Its
# shape must satisfy `verbs/propose.py`'s `ProposedJudge` schema
# (prompt/threshold/label) *and*, once promoted/backtested, be scorable
# by `verbs/backtest.py`'s `_score_run` deterministic `contains` path —
# so `judge_kind` on the persisted draft is forced to "contains" and
# `judge_config["expected"]` is the substring `_score_run` checks for.
DETERMINISTIC_JUDGE_JSON = json.dumps(
    {"prompt": "Flag responses that do not say ok.", "threshold": 0.5, "label": "fail"}
)


def _make_ctx(project_id) -> TenantContext:
    return TenantContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        project_id=project_id,
        api_key_id=uuid4(),
        plan="pro",
        scopes=frozenset({"verbs:*"}),
    )


class _FakeCM:
    """Minimal async context manager for the fake pool's acquire()/transaction()."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """A tiny in-memory stand-in for asyncpg.Pool that backs exactly
    the two tables the loop touches: ``backtest_draft`` and
    ``backtest_run`` (plus ``luna_judge`` for promote). Real SQL is
    never parsed — each verb's queries are matched by a short substring
    the way the existing per-verb unit tests already do, but here the
    fake persists state across verb calls so the loop is a real,
    stateful walk rather than independently-canned mocks.
    """

    def __init__(self):
        self.drafts: dict = {}
        self.runs: dict = {}
        self.judges: dict = {}
        self._judge_by_slug: dict[tuple, object] = {}
        self.alert_rules: dict = {}
        self._alert_rule_by_subject_metric: dict[tuple, object] = {}

    def acquire(self):
        # promote_to_recurring now runs its writes in one transaction:
        # ``async with pool.acquire() as conn, conn.transaction():``. The fake
        # is its own "connection" (it already exposes fetchrow/execute), so
        # acquire just hands back self.
        return _FakeCM(self)

    def transaction(self):
        # No-op transaction/savepoint context for the in-memory fake.
        return _FakeCM(None)

    async def fetchrow(self, query: str, *args):
        q = query.strip()
        if "insert into backtest_draft" in q:
            (
                project_id,
                org_id,
                cluster_ref,
                judge_kind,
                judge_config,
                status,
                created_by,
                created_at,
            ) = args
            draft_id = uuid4()
            row = {
                "id": draft_id,
                "project_id": project_id,
                "org_id": org_id,
                "cluster_ref": cluster_ref,
                "judge_kind": judge_kind,
                "judge_config": judge_config,
                "status": status,
                "created_by": created_by,
                "created_at": created_at,
                "heartbeat_at": None,
                "error": None,
            }
            self.drafts[draft_id] = row
            return {"id": draft_id, "created_at": created_at}

        if "select id, project_id, org_id, cluster_ref, judge_kind, judge_config" in q and (
            "from backtest_draft" in q
        ):
            draft_id = args[0]
            return self.drafts.get(draft_id)

        if "select id, project_id, org_id, judge_kind, judge_config" in q and (
            "from backtest_draft" in q
        ):
            draft_id = args[0]
            draft = self.drafts.get(draft_id)
            if draft is None:
                return None
            return {
                "id": draft["id"],
                "project_id": draft["project_id"],
                "org_id": draft["org_id"],
                "judge_kind": draft["judge_kind"],
                "judge_config": draft["judge_config"],
                "status": draft["status"],
            }

        if "insert into backtest_run" in q:
            draft_id, cohort_size, item_total, window_hours = args
            run_id = uuid4()
            row = {
                "id": run_id,
                "draft_id": draft_id,
                "status": "queued",
                "cohort_size": cohort_size,
                "item_total": item_total,
                "item_done": 0,
                "window_hours": window_hours,
                "spans_scanned": 0,
                "cost_usd": 0.0,
                "caught": None,
                "missed": None,
                "would_have_flagged_at": None,
                "heartbeat_at": None,
                "error": None,
            }
            self.runs[run_id] = row
            return {"id": run_id, "status": "queued"}

        if "select id, draft_id, status, cohort_size, item_total, window_hours" in q:
            run_id = args[0]
            return self.runs.get(run_id)

        if "select br.id, br.status" in q:
            run_id = args[0]
            run = self.runs.get(run_id)
            if run is None:
                return None
            draft = self.drafts.get(run["draft_id"])
            return {
                "id": run["id"],
                "status": run["status"],
                "caught": run["caught"],
                "missed": run["missed"],
                "error": run["error"],
                "heartbeat_at": run["heartbeat_at"],
                "project_id": draft["project_id"],
            }

        if "insert into luna_judge" in q:
            # promote stamps the recurring cadence: ... model, created_by,
            # schedule_seconds (is_recurring/recurring_enabled/scored_through
            # are SQL literals, not bound args).
            (
                project_id,
                slug,
                name,
                description,
                prompt,
                output_format,
                provider,
                model,
                _cb,
                schedule_seconds,
            ) = args
            key = (project_id, slug)
            if key in self._judge_by_slug:
                import asyncpg

                raise asyncpg.UniqueViolationError("duplicate key")
            judge_id = uuid4()
            self._judge_by_slug[key] = judge_id
            self.judges[judge_id] = {
                "project_id": project_id,
                "slug": slug,
                "prompt": prompt,
                "schedule_seconds": schedule_seconds,
                "is_recurring": True,
            }
            return {"id": judge_id}

        if "select id from luna_judge" in q:
            project_id, slug = args
            judge_id = self._judge_by_slug.get((project_id, slug))
            if judge_id is None:
                return None
            return {"id": judge_id}

        raise AssertionError(f"unexpected fetchrow query: {q!r}")

    async def execute(self, query: str, *args):
        q = query.strip()
        if "update backtest_run" in q and "heartbeat_at" in q and "spans_scanned" in q:
            run_id, item_done, spans_scanned, cost_usd = args
            run = self.runs[run_id]
            run["item_done"] = item_done
            run["spans_scanned"] = spans_scanned
            run["cost_usd"] = cost_usd
            return "UPDATE 1"
        if "set status='running'" in q:
            (run_id,) = args
            self.runs[run_id]["status"] = "running"
            return "UPDATE 1"
        if "set status='done'" in q and "cohort_size=0" in q:
            (run_id,) = args
            run = self.runs[run_id]
            run.update(status="done", cohort_size=0, item_total=0, item_done=0, caught=0, missed=0)
            return "UPDATE 1"
        if "set status='done'" in q:
            run_id, caught, missed, would_have_flagged_at = args
            run = self.runs[run_id]
            run.update(
                status="done",
                caught=caught,
                missed=missed,
                would_have_flagged_at=would_have_flagged_at,
            )
            return "UPDATE 1"
        if "set status='failed'" in q:
            run_id, reason = args
            run = self.runs[run_id]
            run.update(status="failed", error=reason)
            return "UPDATE 1"
        if "update backtest_draft" in q:
            draft_id, status = args
            self.drafts[draft_id]["status"] = status
            return "UPDATE 1"
        if "update backtest_run" in q and "status = 'failed'" in q:
            run_id, error = args
            run = self.runs[run_id]
            run.update(status="failed", error=error)
            return "UPDATE 1"
        if "insert into alert_rule" in q:
            (
                project_id,
                name,
                comparator,
                threshold,
                window_seconds,
                subject_id,
                created_by,
            ) = args
            key = (subject_id, "judge_score_avg")
            if key in self._alert_rule_by_subject_metric:
                return "INSERT 0 0"  # on conflict ... do nothing
            rule_id = uuid4()
            self._alert_rule_by_subject_metric[key] = rule_id
            self.alert_rules[rule_id] = {
                "project_id": project_id,
                "name": name,
                "metric": "judge_score_avg",
                "comparator": comparator,
                "threshold": threshold,
                "window_seconds": window_seconds,
                "subject_id": subject_id,
                "enabled": True,
                "created_by": created_by,
            }
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute query: {q!r}")


class _FakeClickHouse:
    """Backs the ClickHouse-side reads of the loop: the failure-cluster
    triage query, the propose-eval sample-fetch, the cohort count, and
    the cohort select. All keyed off the fixed fixture below so the
    backtest's caught/missed numbers are exactly reproducible.
    """

    def __init__(self, cluster_rows, sample_rows, cohort_rows):
        self._cluster_rows = cluster_rows
        self._sample_rows = sample_rows
        self._cohort_rows = cohort_rows
        self.insert_calls: list = []

    async def query(self, sql: str, *, parameters: dict):
        s = sql.strip()
        if "from run final" in s and "group by key" in s:
            return self._cluster_rows
        if "select run_id, inputs, outputs, error_kind" in s:
            return self._sample_rows
        if "select count() as total" in s:
            return [{"total": len(self._cohort_rows)}]
        if "select run_id, status, start_time" in s:
            return self._cohort_rows
        raise AssertionError(f"unexpected ClickHouse query: {s!r}")

    async def insert(self, table: str, rows: list, *, column_names: list[str]):
        self.insert_calls.append((table, rows, column_names))


def _fixture(project_id):
    """A fixed, deterministic set of 3 failing runs: 2 whose output
    contains "ok" (the judge should catch these as NOT failing... wait,
    see note) and 1 that doesn't.

    Judge semantics (verbs/backtest.py `_score_run`, kind="contains"):
    score=1.0 ("pass") if `expected` is a substring of `outputs`, else
    0.0 ("fail"). A run is "caught" by the backtest when
    `outcome == "ok" and score <= FLAG_THRESHOLD` (0.5) — i.e. caught
    means the judge scored it as failing/flagged. With
    `expected="ok"`, the 2 runs whose output contains "ok" score 1.0
    (NOT caught/flagged), and the 1 run without "ok" scores 0.0
    (caught/flagged). So this fixture reproducibly yields caught=1,
    missed=2.
    """
    run_id_1, run_id_2, run_id_3 = uuid4(), uuid4(), uuid4()
    cluster_rows = [
        {
            "key": "TimeoutError",
            "count": 3,
            "sample_run_ids": [run_id_1, run_id_2, run_id_3],
        }
    ]
    sample_rows = [
        {
            "run_id": run_id_1,
            "inputs": "do the thing",
            "outputs": "everything is ok",
            "error_kind": "TimeoutError",
        },
        {
            "run_id": run_id_2,
            "inputs": "do the thing",
            "outputs": "still ok here",
            "error_kind": "TimeoutError",
        },
        {
            "run_id": run_id_3,
            "inputs": "do the thing",
            "outputs": "total failure",
            "error_kind": "TimeoutError",
        },
    ]
    t0 = datetime.now(UTC)
    cohort_rows = [
        {
            "project_id": str(project_id),
            "run_id": run_id_1,
            "status": "error",
            "start_time": t0,
            "error_kind": "TimeoutError",
            "name": "agent-step",
            "inputs": "do the thing",
            "outputs": "everything is ok",
            "total_tokens": 100,
            "cost_usd": 0.01,
        },
        {
            "project_id": str(project_id),
            "run_id": run_id_2,
            "status": "error",
            "start_time": t0,
            "error_kind": "TimeoutError",
            "name": "agent-step",
            "inputs": "do the thing",
            "outputs": "still ok here",
            "total_tokens": 100,
            "cost_usd": 0.01,
        },
        {
            "project_id": str(project_id),
            "run_id": run_id_3,
            "status": "error",
            "start_time": t0,
            "error_kind": "TimeoutError",
            "name": "agent-step",
            "inputs": "do the thing",
            "outputs": "total failure",
            "total_tokens": 100,
            "cost_usd": 0.01,
        },
    ]
    return cluster_rows, sample_rows, cohort_rows


async def _run_full_loop(mocker, ctx: TenantContext, deps: VerbDeps):
    """Walk the entire loop for one TenantContext, returning every
    intermediate output so callers can assert on the full chain."""
    from langprobe_api.verbs import backtest as backtest_mod

    # 1. cluster_failures
    cluster_out = await cluster_failures(
        deps, ctx, ClusterFailuresIn(project_id=ctx.project_id, window_hours=24, group_by="error")
    )
    assert len(cluster_out.clusters) == 1
    cluster = cluster_out.clusters[0]

    # 2. propose_eval — from the cluster's own sample_run_ids/key.
    # The LLM proposer is mocked to hand back a JSON rubric that, once
    # persisted, IS scored deterministically by the "contains" path —
    # so we patch _draft_via_llm AND force judge_kind post-hoc via the
    # same call (propose.py always writes JUDGE_KIND="luna:proposed";
    # the fake pool doesn't care about judge_kind's value for the
    # propose step, but _run_backtest's deterministic scoring needs
    # judge_kind="contains" + judge_config["expected"]="ok" on the
    # persisted draft, so we override it directly on the fake store
    # after propose_eval returns, mirroring an operator editing the
    # draft's judge_kind before backtesting — the loop's *shape* is
    # what's under test, not propose.py's own JUDGE_KIND choice, which
    # `test_verb_propose.py` already covers). The draft's STATUS,
    # however, is left entirely to the real lifecycle wiring below —
    # propose_eval persists DRAFTING, run_judge_over_cohort transitions
    # it to BACKTESTING, and _run_backtest transitions it to READY on
    # successful completion. Nothing here hand-sets draft status.
    mocker.patch(
        "langprobe_api.verbs.propose._draft_via_llm",
        mocker.AsyncMock(return_value=DETERMINISTIC_JUDGE_JSON),
    )
    propose_out = await propose_eval(
        deps,
        ctx,
        ProposeEvalIn(
            project_id=ctx.project_id,
            sample_run_ids=cluster.sample_run_ids,
            group_key=cluster.key,
        ),
    )
    assert propose_out.status == DraftStatus.DRAFTING

    # Force the persisted draft onto the deterministic "contains" judge
    # kind so `_run_backtest` scores it reproducibly (see note above).
    # This does NOT touch draft status.
    stored_draft = deps.pool.drafts[propose_out.draft_id]
    stored_draft["judge_kind"] = "contains"
    stored_draft["judge_config"] = {"expected": "ok"}

    # 3. run_judge_over_cohort (setup) — from propose_eval's draft_id.
    # This is the REAL transition DRAFTING -> BACKTESTING.
    backtest_out = await run_judge_over_cohort(
        deps, ctx, BacktestIn(draft_id=propose_out.draft_id, window_hours=24)
    )
    assert backtest_out.status == BacktestStatus.QUEUED
    assert stored_draft["status"] == DraftStatus.BACKTESTING.value

    # 3b. _run_backtest (executor) — the background half. On successful
    # completion this is the REAL transition BACKTESTING -> READY (no
    # hand-set status hack) — the fake pool applies the same
    # `update backtest_draft` statement backtest.py issues in
    # production.
    await backtest_mod._run_backtest(deps, backtest_out.backtest_run_id)
    assert stored_draft["status"] == DraftStatus.READY.value

    # 4. promote_to_recurring — from the same draft_id, now backtested
    # for real.
    promote_out = await promote_to_recurring(
        deps,
        ctx,
        PromoteIn(draft_id=propose_out.draft_id, approval_token="approved-by-alice"),
    )

    # 5. watch_judge — from run_judge_over_cohort's backtest_run_id.
    watch_out = await watch_judge(deps, ctx, WatchIn(target_id=backtest_out.backtest_run_id))

    return {
        "cluster_out": cluster_out,
        "propose_out": propose_out,
        "backtest_out": backtest_out,
        "promote_out": promote_out,
        "watch_out": watch_out,
    }


@pytest.fixture
def loop_env(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    cluster_rows, sample_rows, cohort_rows = _fixture(project_id)
    pool = _FakePool()
    ch = _FakeClickHouse(cluster_rows, sample_rows, cohort_rows)
    deps = VerbDeps(pool=pool, ch=ch)
    return project_id, ctx, deps


async def test_loop_composes_each_output_feeds_the_next_input(mocker, loop_env):
    project_id, ctx, deps = loop_env

    result = await _run_full_loop(mocker, ctx, deps)

    cluster_out = result["cluster_out"]
    propose_out = result["propose_out"]
    backtest_out = result["backtest_out"]
    promote_out = result["promote_out"]
    watch_out = result["watch_out"]

    # cluster_failures -> propose_eval: the sample_run_ids/group_key
    # propose_eval consumed are exactly what the cluster produced.
    assert len(cluster_out.clusters[0].sample_run_ids) == 3

    # propose_eval -> run_judge_over_cohort: a real draft_id, drafting
    # status (it still needs a completed backtest before it's ready).
    assert propose_out.draft_id is not None
    assert propose_out.status == DraftStatus.DRAFTING

    # run_judge_over_cohort -> _run_backtest -> watch_judge: the same
    # backtest_run_id flows through and ends up "done".
    assert backtest_out.backtest_run_id is not None
    assert watch_out.status == "done"
    assert watch_out.caught is not None and watch_out.missed is not None

    # promote_to_recurring -> a real judge_id, independent of watch.
    assert promote_out.judge_id is not None


async def test_loop_deterministic_backtest_is_stable_and_reproducible(mocker):
    """Running the identical fixture through the loop twice (fresh
    TenantContext + fresh fake store each time, same underlying data)
    must yield the exact same caught/missed split both times."""
    results = []
    for _ in range(2):
        project_id = uuid4()
        ctx = _make_ctx(project_id)
        cluster_rows, sample_rows, cohort_rows = _fixture(project_id)
        pool = _FakePool()
        ch = _FakeClickHouse(cluster_rows, sample_rows, cohort_rows)
        deps = VerbDeps(pool=pool, ch=ch)
        result = await _run_full_loop(mocker, ctx, deps)
        results.append(result["watch_out"])

    assert results[0].status == results[1].status == "done"
    assert results[0].caught == results[1].caught == 1
    assert results[0].missed == results[1].missed == 2


async def test_loop_rejects_cross_project_context_at_every_verb(mocker, loop_env):
    """A TenantContext scoped to a DIFFERENT project than the one the
    fixture's cluster/draft/backtest_run actually belong to must be
    rejected with ScopeError at cluster_failures, propose_eval,
    run_judge_over_cohort, promote_to_recurring, and watch_judge alike
    — not just the first verb in the chain."""
    project_id, real_ctx, deps = loop_env
    other_project_ctx = _make_ctx(uuid4())

    # cluster_failures: wrong project_id in the request itself.
    with pytest.raises(ScopeError):
        await cluster_failures(
            deps,
            other_project_ctx,
            ClusterFailuresIn(project_id=project_id, window_hours=24, group_by="error"),
        )

    # Run the real loop far enough (under the REAL ctx) to get a real
    # draft_id / backtest_run_id to probe with the wrong ctx.
    mocker.patch(
        "langprobe_api.verbs.propose._draft_via_llm",
        mocker.AsyncMock(return_value=DETERMINISTIC_JUDGE_JSON),
    )
    cluster_out = await cluster_failures(
        deps,
        real_ctx,
        ClusterFailuresIn(project_id=project_id, window_hours=24, group_by="error"),
    )
    cluster = cluster_out.clusters[0]

    # propose_eval: wrong ctx, correct project_id in the body -> ScopeError
    # (require_project_scope compares ctx.project_id to params.project_id).
    with pytest.raises(ScopeError):
        await propose_eval(
            deps,
            other_project_ctx,
            ProposeEvalIn(
                project_id=project_id,
                sample_run_ids=cluster.sample_run_ids,
                group_key=cluster.key,
            ),
        )

    propose_out = await propose_eval(
        deps,
        real_ctx,
        ProposeEvalIn(
            project_id=project_id,
            sample_run_ids=cluster.sample_run_ids,
            group_key=cluster.key,
        ),
    )
    stored_draft = deps.pool.drafts[propose_out.draft_id]
    stored_draft["judge_kind"] = "contains"
    stored_draft["judge_config"] = {"expected": "ok"}

    # run_judge_over_cohort: wrong ctx, real draft_id -> ScopeError
    # (draft's project_id doesn't match other_project_ctx.project_id).
    with pytest.raises(ScopeError):
        await run_judge_over_cohort(
            deps, other_project_ctx, BacktestIn(draft_id=propose_out.draft_id, window_hours=24)
        )

    backtest_out = await run_judge_over_cohort(
        deps, real_ctx, BacktestIn(draft_id=propose_out.draft_id, window_hours=24)
    )
    from langprobe_api.verbs.backtest import _run_backtest

    await _run_backtest(deps, backtest_out.backtest_run_id)
    # _run_backtest transitions the draft BACKTESTING -> READY for real
    # on successful completion — no hand-set status hack needed.
    assert stored_draft["status"] == DraftStatus.READY.value

    # promote_to_recurring: wrong ctx, real draft_id -> ScopeError.
    with pytest.raises(ScopeError):
        await promote_to_recurring(
            deps,
            other_project_ctx,
            PromoteIn(draft_id=propose_out.draft_id, approval_token="approved-by-alice"),
        )

    # watch_judge: wrong ctx, real backtest_run_id -> ScopeError.
    with pytest.raises(ScopeError):
        await watch_judge(deps, other_project_ctx, WatchIn(target_id=backtest_out.backtest_run_id))

    # Sanity: the REAL ctx still succeeds at every one of those same calls.
    promote_out = await promote_to_recurring(
        deps,
        real_ctx,
        PromoteIn(draft_id=propose_out.draft_id, approval_token="approved-by-alice"),
    )
    assert promote_out.judge_id is not None
    watch_out = await watch_judge(deps, real_ctx, WatchIn(target_id=backtest_out.backtest_run_id))
    assert watch_out.status == "done"


# --------------------------------------------------------------------------
# DB-backed: promote_to_recurring auto-provisions the judge_score_avg watch
# rule against a real Postgres (Task 5). Guarded by LANGPROBE_TEST_DSN so it
# skips cleanly when no integration DB is configured.
# --------------------------------------------------------------------------


def _dsn() -> str:
    dsn = os.environ.get("LANGPROBE_TEST_DSN")
    if not dsn:
        pytest.skip("set LANGPROBE_TEST_DSN to run integration tests")
    return dsn


async def _insert_project(pool: asyncpg.Pool) -> object:
    """Seed the org -> workspace -> project FK chain backtest_draft/
    luna_judge/alert_rule all require."""
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
    project_id = await pool.fetchval(
        "insert into project (workspace_id, slug, name) values ($1, $2, $3) returning id",
        workspace_id,
        f"proj-{suffix}",
        "test project",
    )
    return org_id, project_id


async def _insert_app_user(pool: asyncpg.Pool) -> object:
    """Seed a real app_user so luna_judge.created_by / alert_rule.created_by
    FKs are satisfiable (both reference app_user, not the abstract
    api-key concept `TenantContext.api_key_id` stands in for elsewhere)."""
    suffix = uuid4().hex[:12]
    return await pool.fetchval(
        "insert into app_user (email, password_hash) values ($1, $2) returning id",
        f"user-{suffix}@example.com",
        "x" * 40,
    )


async def _insert_ready_draft(pool: asyncpg.Pool, *, org_id, project_id) -> object:
    """A backtest_draft already in `ready` status — the only status
    promote_to_recurring accepts."""
    return await pool.fetchval(
        """
        insert into backtest_draft (
            project_id, org_id, cluster_ref, judge_kind, judge_config, status
        )
        values ($1, $2, '{}'::jsonb, 'luna:proposed', $3, 'ready')
        returning id
        """,
        project_id,
        org_id,
        {"prompt": "flag hallucinations", "threshold": 0.5, "label": "fail"},
    )


async def _init_jsonb_codec(con: asyncpg.Connection) -> None:
    """promote_to_recurring reads `backtest_draft.judge_config` (jsonb)
    back out as a dict (`judge_config.get("prompt", "")`); asyncpg has no
    default jsonb codec, so an ad hoc pool must register one — mirroring
    what the app's own connection setup is expected to provide."""
    await con.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def test_promote_provisions_watch_rule_idempotently_against_real_db() -> None:
    pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=4, init=_init_jsonb_codec)
    try:
        org_id, project_id = await _insert_project(pool)
        user_id = await _insert_app_user(pool)
        draft_id = await _insert_ready_draft(pool, org_id=org_id, project_id=project_id)
        deps = VerbDeps(pool=pool, ch=None)
        ctx = TenantContext(
            org_id=org_id,
            workspace_id=uuid4(),
            project_id=project_id,
            api_key_id=user_id,
            plan="pro",
            scopes=frozenset({"verbs:*"}),
        )
        same_params = PromoteIn(
            draft_id=draft_id, approval_token="approved-by-alice", schedule_seconds=1800
        )

        promote_out = await promote_to_recurring(deps, ctx, same_params)
        judge_id = promote_out.judge_id

        rules = await pool.fetch(
            "select metric, comparator, threshold, subject_id, window_seconds "
            "from alert_rule where subject_id = $1",
            judge_id,
        )
        assert len(rules) == 1
        assert rules[0]["metric"] == "judge_score_avg"
        assert rules[0]["comparator"] == "<"
        assert float(rules[0]["threshold"]) == 0.5
        assert rules[0]["window_seconds"] == 5400  # 3x the 1800s cadence

        # backtest_draft only accepts a promote when its status is `ready`,
        # so flip it back to re-exercise a retried promote of the same
        # draft/config -> still exactly one alert_rule for that subject.
        await pool.execute("update backtest_draft set status = 'ready' where id = $1", draft_id)
        await promote_to_recurring(deps, ctx, same_params)
        rules2 = await pool.fetch("select id from alert_rule where subject_id = $1", judge_id)
        assert len(rules2) == 1
    finally:
        await pool.close()
