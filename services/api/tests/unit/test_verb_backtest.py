"""run_judge_over_cohort verb (Task 4, D2-A).

Two halves under test:
- ``run_judge_over_cohort`` (sync-ish setup): scope check, draft lookup,
  cohort-size clamp, queued row insert.
- ``_run_backtest`` (executor): mirrors ``evals.py``'s ``_run_eval`` —
  select the cohort, score each item with a deterministic judge, write
  one ``backtest_score`` row per item to the ClickHouse scratch store,
  heartbeat + item_done per item, enforce hard caps mid-loop, and roll
  up caught/missed/would_have_flagged_at at the end.

Everything here mocks ``deps.pool`` (asyncpg) and ``deps.ch``
(ClickHouseQuery) — no real DB. The `contains` judge is deterministic
so caught/missed numbers are stable and assertable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langprobe_api.verbs.backtest import (
    COST_CEILING_USD,
    MAX_COHORT,
    _run_backtest,
    run_judge_over_cohort,
)
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.lifecycle import BacktestStatus
from langprobe_api.verbs.models import BacktestIn
from langprobe_api.verbs.scope import ScopeError
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


def _make_pool(mocker, *, draft_row=None, fetchrow_rows=None):
    """asyncpg.Pool double. ``fetchrow_rows`` lets a test queue up
    multiple sequential fetchrow returns (draft lookup, then run lookup
    in the executor, etc.) when a single canned row isn't enough."""
    pool = mocker.MagicMock(name="pool")
    if fetchrow_rows is not None:
        pool.fetchrow = mocker.AsyncMock(side_effect=fetchrow_rows)
    else:
        pool.fetchrow = mocker.AsyncMock(return_value=draft_row)
    pool.execute = mocker.AsyncMock(return_value="UPDATE 1")
    pool.fetch = mocker.AsyncMock(return_value=[])
    return pool


def _make_ch(mocker, *, cohort_rows=None):
    ch = mocker.MagicMock(name="ch")
    ch.query = mocker.AsyncMock(return_value=cohort_rows or [])
    ch.insert = mocker.AsyncMock(return_value=None)
    return ch


def _draft_row(
    project_id, org_id=None, judge_kind="contains", judge_config=None, status="backtesting"
):
    """Default status is ``backtesting`` — the realistic precondition
    for ``_run_backtest`` (the executor), which is only ever invoked
    after ``run_judge_over_cohort`` has already transitioned the draft
    out of ``drafting``. Tests of ``run_judge_over_cohort`` itself
    (the setup half) pass ``status="drafting"`` explicitly."""
    return {
        "id": uuid4(),
        "project_id": project_id,
        "org_id": org_id or uuid4(),
        "cluster_ref": {},
        "judge_kind": judge_kind,
        "judge_config": judge_config if judge_config is not None else {"expected": "ok"},
        "status": status,
        "created_by": uuid4(),
        "created_at": datetime.now(UTC),
        "heartbeat_at": None,
        "error": None,
    }


def _cohort_row(run_id=None, outputs="everything is ok here", start_time=None):
    return {
        "project_id": None,
        "run_id": run_id or uuid4(),
        "status": "error",
        "start_time": start_time or datetime.now(UTC),
        "error_kind": "TimeoutError",
        "name": "agent-step",
        "inputs": "do the thing",
        "outputs": outputs,
        "total_tokens": 100,
        "cost_usd": 0.01,
    }


def _backtest_run_row(run_id, draft_id, **overrides):
    row = {
        "id": run_id,
        "draft_id": draft_id,
        "status": "queued",
        "cohort_size": 3,
        "spans_scanned": 0,
        "cost_usd": 0.0,
        "caught": None,
        "missed": None,
        "would_have_flagged_at": None,
        "item_total": 3,
        "item_done": 0,
        "window_hours": 720,
        "heartbeat_at": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    row.update(overrides)
    return row


# ----- run_judge_over_cohort (setup half) -----------------------------------


async def test_run_judge_over_cohort_scope_mismatch_raises_and_inserts_nothing(mocker):
    ctx = _make_ctx(uuid4())
    draft = _draft_row(project_id=uuid4())  # different project
    pool = _make_pool(mocker, draft_row=draft)
    ch = _make_ch(mocker)
    deps = VerbDeps(pool=pool, ch=ch)
    params = BacktestIn(draft_id=draft["id"], window_hours=24)

    with pytest.raises(ScopeError):
        await run_judge_over_cohort(deps, ctx, params)

    insert_calls = [
        c for c in pool.execute.await_args_list if "insert into backtest_run" in c.args[0]
    ]
    assert insert_calls == []


async def test_run_judge_over_cohort_missing_draft_raises_scope_error(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    pool = _make_pool(mocker, draft_row=None)
    ch = _make_ch(mocker)
    deps = VerbDeps(pool=pool, ch=ch)
    params = BacktestIn(draft_id=uuid4(), window_hours=24)

    with pytest.raises(ScopeError):
        await run_judge_over_cohort(deps, ctx, params)


async def test_run_judge_over_cohort_inserts_queued_row(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id=project_id, status="drafting")
    inserted_row = {
        "id": uuid4(),
        "cohort_size": 3,
        "status": "queued",
    }
    pool = _make_pool(mocker, fetchrow_rows=[draft, inserted_row])
    ch = _make_ch(mocker, cohort_rows=[{"total": 3}])
    deps = VerbDeps(pool=pool, ch=ch)
    params = BacktestIn(draft_id=draft["id"], window_hours=24)

    out = await run_judge_over_cohort(deps, ctx, params)

    assert out.backtest_run_id == inserted_row["id"]
    assert out.status == BacktestStatus.QUEUED


async def test_run_judge_over_cohort_transitions_draft_drafting_to_backtesting(mocker):
    """After queuing a backtest_run, the draft must move
    DRAFTING -> BACKTESTING so promote_to_recurring's READY gate
    remains meaningful (it can't be satisfied by propose_eval alone)."""
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id=project_id, status="drafting")
    inserted_row = {"id": uuid4(), "cohort_size": 3, "status": "queued"}
    pool = _make_pool(mocker, fetchrow_rows=[draft, inserted_row])
    ch = _make_ch(mocker, cohort_rows=[{"total": 3}])
    deps = VerbDeps(pool=pool, ch=ch)
    params = BacktestIn(draft_id=draft["id"], window_hours=24)

    await run_judge_over_cohort(deps, ctx, params)

    transition_calls = [
        c for c in pool.execute.await_args_list if "update backtest_draft" in c.args[0]
    ]
    assert len(transition_calls) == 1
    _, draft_id_arg, status_arg = transition_calls[0].args
    assert draft_id_arg == draft["id"]
    assert status_arg == "backtesting"


async def test_run_judge_over_cohort_clamps_cohort_to_max(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id=project_id, status="drafting")
    inserted_row = {"id": uuid4(), "cohort_size": MAX_COHORT, "status": "queued"}
    pool = _make_pool(mocker, fetchrow_rows=[draft, inserted_row])
    # ClickHouse count query says there are way more than MAX_COHORT candidates.
    ch = _make_ch(mocker, cohort_rows=[{"total": MAX_COHORT + 250}])
    deps = VerbDeps(pool=pool, ch=ch)
    params = BacktestIn(draft_id=draft["id"], window_hours=24)

    await run_judge_over_cohort(deps, ctx, params)

    insert_call = next(
        c for c in pool.fetchrow.await_args_list if "insert into backtest_run" in c.args[0]
    )
    # cohort_size positional arg is whatever the query builder clamps to.
    assert MAX_COHORT in insert_call.args
    assert (MAX_COHORT + 250) not in insert_call.args


# ----- _run_backtest (executor half) ----------------------------------------


async def test_run_backtest_uses_callers_window_hours_not_hardcoded_720(mocker):
    """The cohort selection window MUST match what run_judge_over_cohort
    sized and returned to the caller (window_hours=24), not a hardcoded
    720h — otherwise the executed cohort silently diverges from the
    cohort the caller was shown."""
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id)
    run_row = _backtest_run_row(backtest_run_id, draft_id, window_hours=24)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[])
    deps = VerbDeps(pool=pool, ch=ch)

    before = datetime.now(UTC)
    await _run_backtest(deps, backtest_run_id)
    after = datetime.now(UTC)

    ch.query.assert_awaited_once()
    _, kwargs = ch.query.await_args.args, ch.query.await_args.kwargs
    since = kwargs["parameters"]["since"]

    # since should be ~24h ago (bounded by the wall-clock window this
    # test ran in), not ~720h ago.
    assert (before - timedelta(hours=24)) <= since <= (after - timedelta(hours=24))
    assert since > before - timedelta(hours=720)


async def test_run_backtest_deterministic_contains_judge_stable_caught_missed(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(
        project_id=project_id, judge_kind="contains", judge_config={"expected": "ok"}
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)

    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])

    matching = _cohort_row(outputs="all systems ok")
    also_matching = _cohort_row(outputs="still ok now")
    not_matching = _cohort_row(outputs="total failure")
    ch = _make_ch(mocker, cohort_rows=[matching, also_matching, not_matching])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    # One backtest_score row inserted per cohort run.
    assert ch.insert.await_count == 1
    (table, rows), kwargs = ch.insert.await_args.args, ch.insert.await_args.kwargs
    assert table == "backtest_score"
    assert len(rows) == 3
    column_names = kwargs["column_names"]
    assert "draft_id" in column_names
    assert "outcome" in column_names

    # Final status update: 2 caught (score 1.0 <= FLAG_THRESHOLD? no —
    # caught means the judge FLAGGED it, i.e. score <= FLAG_THRESHOLD).
    final_call = next(
        c
        for c in pool.execute.await_args_list
        if "status='done'" in c.args[0] or "status=$" in c.args[0] and "done" in str(c.args)
    )
    assert final_call is not None


async def test_run_backtest_writes_stable_caught_missed_values(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(
        project_id=project_id, judge_kind="contains", judge_config={"expected": "ok"}
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])

    t0 = datetime.now(UTC) - timedelta(hours=2)
    t1 = datetime.now(UTC) - timedelta(hours=1)
    matching = _cohort_row(outputs="all systems ok", start_time=t0)
    not_matching_1 = _cohort_row(outputs="total failure", start_time=t1)
    not_matching_2 = _cohort_row(outputs="another failure", start_time=datetime.now(UTC))
    ch = _make_ch(mocker, cohort_rows=[matching, not_matching_1, not_matching_2])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1
    # args: (sql, backtest_run_id, caught, missed, would_have_flagged_at)
    _, _, caught, missed, would_have_flagged_at = done_calls[0].args
    assert caught == 2
    assert missed == 1
    assert would_have_flagged_at == t1


async def test_run_backtest_empty_cohort_marks_done_not_failed(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id)
    run_row = _backtest_run_row(backtest_run_id, draft_id, cohort_size=0, item_total=0)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    failed_calls = [c for c in pool.execute.await_args_list if "'failed'" in c.args[0]]
    assert failed_calls == []
    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1
    ch.insert.assert_not_awaited()

    # Empty-cohort success still counts as "backtested" -> draft moves
    # BACKTESTING -> READY so promote_to_recurring's gate is satisfiable.
    draft_transition_calls = [
        c for c in pool.execute.await_args_list if "update backtest_draft" in c.args[0]
    ]
    assert len(draft_transition_calls) == 1
    _, transitioned_draft_id, transitioned_status = draft_transition_calls[0].args
    assert transitioned_draft_id == draft["id"]
    assert transitioned_status == "ready"


async def test_run_backtest_successful_completion_transitions_draft_to_ready(mocker):
    """On a normal (non-empty-cohort) successful `done` finalize, the
    draft must transition BACKTESTING -> READY so promote_to_recurring
    now accepts it — this is the crux of the lifecycle wiring fix."""
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(
        project_id=project_id,
        judge_kind="contains",
        judge_config={"expected": "ok"},
        status="backtesting",
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[_cohort_row(outputs="all ok")])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1

    draft_transition_calls = [
        c for c in pool.execute.await_args_list if "update backtest_draft" in c.args[0]
    ]
    assert len(draft_transition_calls) == 1
    _, transitioned_draft_id, transitioned_status = draft_transition_calls[0].args
    assert transitioned_draft_id == draft["id"]
    assert transitioned_status == "ready"


async def test_run_backtest_cap_exceeded_leaves_draft_in_backtesting(mocker):
    """A `failed` (cap-exceeded) backtest must NOT promote the draft to
    READY — it stays in BACKTESTING so promote_to_recurring keeps
    rejecting it until a fresh, successful backtest completes."""
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id, status="backtesting")
    run_row = _backtest_run_row(backtest_run_id, draft_id, cohort_size=3, item_total=3)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[_cohort_row(), _cohort_row(), _cohort_row()])
    deps = VerbDeps(pool=pool, ch=ch)

    mocker.patch("langprobe_api.verbs.backtest.MAX_SPANS", 1)

    await _run_backtest(deps, backtest_run_id)

    failed_calls = [c for c in pool.execute.await_args_list if "'failed'" in c.args[0]]
    assert len(failed_calls) == 1

    # No draft-status UPDATE at all on the failed path — draft stays in
    # whatever state it already had (BACKTESTING).
    draft_transition_calls = [
        c for c in pool.execute.await_args_list if "update backtest_draft" in c.args[0]
    ]
    assert draft_transition_calls == []


async def test_run_backtest_judge_error_on_one_item_continues_with_partial_results(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    # Unknown judge_kind on a specific "config" won't error in our
    # deterministic path, so we simulate a judge error via a malformed
    # judge_config that raises when scoring (missing key access).
    draft = _draft_row(
        project_id=project_id, judge_kind="contains", judge_config={"expected": "ok"}
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])

    good_1 = _cohort_row(outputs="all ok")
    bad = _cohort_row(outputs=None)  # None triggers a judge-side error (can't do `in` on None)
    good_2 = _cohort_row(outputs="still ok")
    ch = _make_ch(mocker, cohort_rows=[good_1, bad, good_2])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    assert ch.insert.await_count == 1
    rows = ch.insert.await_args.args[1]
    outcomes = [row[ch.insert.await_args.kwargs["column_names"].index("outcome")] for row in rows]
    assert "judge_unavailable" in outcomes
    assert outcomes.count("ok") == 2

    # Run still completes (done), not failed, despite the one bad item.
    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1


async def test_run_backtest_exceeding_max_spans_marks_failed_cap_exceeded(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id)
    run_row = _backtest_run_row(backtest_run_id, draft_id, cohort_size=3, item_total=3)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[_cohort_row(), _cohort_row(), _cohort_row()])
    deps = VerbDeps(pool=pool, ch=ch)

    mocker.patch("langprobe_api.verbs.backtest.MAX_SPANS", 1)

    await _run_backtest(deps, backtest_run_id)

    failed_calls = [c for c in pool.execute.await_args_list if "'failed'" in c.args[0]]
    assert len(failed_calls) == 1
    assert any("cap_exceeded" in str(a) for a in failed_calls[0].args)


async def test_run_backtest_exceeding_cost_ceiling_marks_failed_cap_exceeded(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id)
    run_row = _backtest_run_row(backtest_run_id, draft_id, cohort_size=3, item_total=3)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    expensive_rows = [_cohort_row() for _ in range(3)]
    for r in expensive_rows:
        r["cost_usd"] = COST_CEILING_USD  # 2nd item's running total exceeds the ceiling
    ch = _make_ch(mocker, cohort_rows=expensive_rows)
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    failed_calls = [c for c in pool.execute.await_args_list if "'failed'" in c.args[0]]
    assert len(failed_calls) == 1
    assert any("cap_exceeded" in str(a) for a in failed_calls[0].args)


async def test_run_backtest_updates_heartbeat_per_item(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(project_id=project_id)
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])
    ch = _make_ch(mocker, cohort_rows=[_cohort_row(), _cohort_row(), _cohort_row()])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    heartbeat_calls = [c for c in pool.execute.await_args_list if "heartbeat_at" in c.args[0]]
    # One heartbeat update per cohort item (3), at minimum.
    assert len(heartbeat_calls) >= 3


async def test_run_backtest_would_have_flagged_at_is_earliest_flagged_start_time(mocker):
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(
        project_id=project_id, judge_kind="contains", judge_config={"expected": "ok"}
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])

    earliest = datetime.now(UTC) - timedelta(hours=5)
    later = datetime.now(UTC) - timedelta(hours=1)
    flagged_early = _cohort_row(outputs="total failure", start_time=earliest)
    flagged_later = _cohort_row(outputs="also broken", start_time=later)
    not_flagged = _cohort_row(outputs="all ok", start_time=datetime.now(UTC))
    ch = _make_ch(mocker, cohort_rows=[flagged_early, flagged_later, not_flagged])
    deps = VerbDeps(pool=pool, ch=ch)

    await _run_backtest(deps, backtest_run_id)

    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1
    assert earliest in done_calls[0].args
    assert later not in done_calls[0].args


# ----- luna:proposed judge path (Fix 1) --------------------------------------


async def test_run_backtest_luna_proposed_draft_scores_for_real(mocker):
    """A `luna:proposed` draft's judge_config is {"prompt", "threshold",
    "label"} (verbs/propose.py's ProposedJudge shape) — NOT a
    luna_judge row. Before this fix, `_score_run`'s luna branch passed
    that raw judge_config straight to `apply_luna_judge`, which reads
    `judge_row["rubric_prompt"]`/["provider"]/["model"] and raised
    KeyError, silently swallowed into outcome="judge_unavailable" for
    every row. This asserts a `luna:proposed` draft now scores for
    real: apply_luna_judge is mocked to return deterministic
    (score, label, rationale, raw) tuples, and the backtest must
    produce actual caught/missed numbers (not judge_unavailable for
    every item), calling apply_luna_judge with surface="eval" and a
    proper judge_row containing rubric_prompt."""
    draft_id = uuid4()
    backtest_run_id = uuid4()
    project_id = uuid4()
    draft = _draft_row(
        project_id=project_id,
        judge_kind="luna:proposed",
        judge_config={"prompt": "Flag responses that time out.", "threshold": 0.5, "label": "fail"},
    )
    run_row = _backtest_run_row(backtest_run_id, draft_id)
    pool = _make_pool(mocker, fetchrow_rows=[run_row, draft])

    flagged = _cohort_row(outputs="total failure")
    not_flagged = _cohort_row(outputs="all systems ok")
    ch = _make_ch(mocker, cohort_rows=[flagged, not_flagged])
    deps = VerbDeps(pool=pool, ch=ch)

    async def fake_apply_luna_judge(judge_row, **kwargs):
        # Deterministic stand-in scoring: flag runs whose output text
        # contains "failure".
        if "failure" in kwargs["output_text"]:
            return 0.0, "fail", "flagged: contains failure", "raw-fail"
        return 1.0, "pass", "not flagged", "raw-pass"

    mock_apply = mocker.patch(
        "langprobe_api.verbs.backtest.luna_judges.apply_luna_judge",
        mocker.AsyncMock(side_effect=fake_apply_luna_judge),
    )

    await _run_backtest(deps, backtest_run_id)

    # Real scoring happened — NOT judge_unavailable for every row.
    assert ch.insert.await_count == 1
    rows = ch.insert.await_args.args[1]
    column_names = ch.insert.await_args.kwargs["column_names"]
    outcome_idx = column_names.index("outcome")
    outcomes = [row[outcome_idx] for row in rows]
    assert outcomes == ["ok", "ok"]
    assert "judge_unavailable" not in outcomes

    done_calls = [c for c in pool.execute.await_args_list if "'done'" in c.args[0]]
    assert len(done_calls) == 1
    _, _, caught, missed, _ = done_calls[0].args
    assert caught == 1
    assert missed == 1

    # apply_luna_judge called with surface="eval" (a valid SurfaceName;
    # "backtest" is NOT) and a judge_row containing rubric_prompt built
    # from the draft's judge_config["prompt"].
    assert mock_apply.await_count == 2
    for call in mock_apply.await_args_list:
        judge_row_arg = call.args[0]
        assert judge_row_arg["rubric_prompt"] == "Flag responses that time out."
        assert "provider" in judge_row_arg
        assert "model" in judge_row_arg
        assert call.kwargs["surface"] == "eval"
        assert call.kwargs["project_id"] == project_id
