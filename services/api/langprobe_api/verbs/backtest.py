"""run_judge_over_cohort verb (Task 4, D2-A) — the backtest runner.

An operator (or agent) has a ``backtest_draft`` — an AI-proposed judge
that hasn't been promoted to a recurring eval yet. This verb runs that
judge over a bounded, most-recent slice of the project's *failing*
history so the operator can see whether the judge would have caught
those failures before trusting it in production.

Split in two, mirroring ``routers/evals.py``'s ``_run_eval``:

- :func:`run_judge_over_cohort` — the SYNC setup half. Scope-checks the
  draft, sizes the cohort (clamped to :data:`MAX_COHORT`), inserts a
  ``queued`` ``backtest_run`` row, transitions the draft
  ``DRAFTING -> BACKTESTING`` (via ``lifecycle.can_transition``), and
  returns. Framework-agnostic on purpose: no FastAPI/BackgroundTasks
  import here — a later task's HTTP router (or the MCP adapter) is
  responsible for scheduling the execution below.
- :func:`_run_backtest` — the EXECUTOR half. Loads the run + draft,
  flips to ``running``, selects the cohort, scores each row with the
  draft's judge, writes one ``backtest_score`` row per item to the
  ClickHouse SCRATCH store, and updates ``heartbeat_at``/``item_done``
  every item so a restart-orphaned run can be told apart from one still
  in flight. Hard caps (spans scanned, cost) are enforced mid-loop —
  exceeding either aborts the run as ``failed`` with a
  ``cap_exceeded:<which>`` error. A per-item judge error never aborts
  the whole run (ER-23): that row is written with
  ``outcome='judge_unavailable'`` and the loop continues. On a
  successful ``done`` finalize (including the empty-cohort case), the
  draft is transitioned ``BACKTESTING -> READY`` — this is what makes
  ``promote_to_recurring``'s READY gate mean "has completed a
  backtest". On ``failed``/cap-exceeded, the draft is left in
  ``BACKTESTING`` so promote stays blocked.

Caps + threshold are tenant-agnostic module constants — they bound a
single backtest run's blast radius regardless of plan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from langprobe_tenant.context import TenantContext

from langprobe_api.routers import luna_judges
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.lifecycle import BacktestStatus, DraftStatus, can_transition
from langprobe_api.verbs.models import BacktestIn, BacktestOut
from langprobe_api.verbs.scope import ScopeError, require_project_scope

# Cohort size cap — a backtest is a bounded sanity check, not an export.
MAX_COHORT = 500

# Hard ceiling on spans scanned in a single run, independent of cohort
# size (a cohort row can fan out to more than one span in a later
# iteration; today it's 1:1, but the cap is enforced on the running
# total either way).
MAX_SPANS = 50_000

# Hard ceiling on judge spend for a single backtest run (LLM-as-judge
# kinds only cost anything; built-in deterministic judges are free but
# still walk through the same accounting).
COST_CEILING_USD = 5.0

# A judge "catches" a failing run when its score is at or below this
# threshold (lower score = more confident it's a real failure).
FLAG_THRESHOLD = 0.5

_COHORT_SQL = """
    select run_id, status, start_time, error_kind, name, inputs, outputs,
           total_tokens, cost_usd
    from run final
    where project_id = {project_id:UUID}
      and status = 'error'
      and start_time >= {since:DateTime64(9)}
    order by start_time desc
    limit {limit:UInt32}
"""


async def run_judge_over_cohort(
    deps: VerbDeps, ctx: TenantContext, params: BacktestIn
) -> BacktestOut:
    """Set up a backtest run: scope-check the draft, size + clamp the
    cohort, insert a ``queued`` row. Does not execute the backtest —
    that's :func:`_run_backtest`, scheduled by the caller (router/MCP)."""
    draft = await deps.pool.fetchrow(
        """
        select id, project_id, org_id, cluster_ref, judge_kind, judge_config,
               status, created_by, created_at, heartbeat_at, error
        from backtest_draft
        where id = $1
        """,
        params.draft_id,
    )
    if draft is None:
        raise ScopeError(f"backtest_draft {params.draft_id} not found")
    require_project_scope(ctx, draft["project_id"])

    clamped_hours = max(params.window_hours, 0)
    since = datetime.now(UTC) - timedelta(hours=clamped_hours)

    count_rows = await deps.ch.query(
        """
        select count() as total
        from run final
        where project_id = {project_id:UUID}
          and status = 'error'
          and start_time >= {since:DateTime64(9)}
        """,
        parameters={"project_id": str(ctx.project_id), "since": since},
    )
    candidate_total = int(count_rows[0]["total"]) if count_rows else 0
    cohort_size = min(candidate_total, MAX_COHORT)

    row = await deps.pool.fetchrow(
        """
        insert into backtest_run (
            draft_id, status, cohort_size, item_total, window_hours
        )
        values ($1, 'queued', $2, $3, $4)
        returning id, status
        """,
        params.draft_id,
        cohort_size,
        cohort_size,
        clamped_hours,
    )
    assert row is not None

    # A draft only reaches BACKTESTING once a backtest has actually been
    # queued for it — this is what makes the READY gate in
    # promote_to_recurring meaningful ("backtested", not just "drafted").
    current_status = DraftStatus(draft["status"])
    if can_transition(current_status, DraftStatus.BACKTESTING):
        await deps.pool.execute(
            """
            update backtest_draft
            set status = $2
            where id = $1
            """,
            draft["id"],
            DraftStatus.BACKTESTING.value,
        )

    return BacktestOut(backtest_run_id=row["id"], status=BacktestStatus(row["status"]))


async def _run_backtest(deps: VerbDeps, backtest_run_id: UUID) -> None:
    """Execute a queued backtest run: score the cohort, write scratch
    rows, roll up caught/missed, finalize done/failed. Never raises —
    any failure is captured into ``backtest_run.error`` via
    :func:`_mark_backtest_failed`."""
    try:
        run = await deps.pool.fetchrow(
            """
            select id, draft_id, status, cohort_size, item_total, window_hours
            from backtest_run where id = $1
            """,
            backtest_run_id,
        )
        if run is None:
            return

        draft = await deps.pool.fetchrow(
            """
            select id, project_id, org_id, judge_kind, judge_config, status
            from backtest_draft where id = $1
            """,
            run["draft_id"],
        )
        if draft is None:
            await _mark_backtest_failed(deps.pool, backtest_run_id, "backtest_draft not found")
            return

        await deps.pool.execute(
            """
            update backtest_run
            set status='running', started_at=now(), heartbeat_at=now()
            where id=$1
            """,
            backtest_run_id,
        )

        window_hours = run["window_hours"]
        since = datetime.now(UTC) - timedelta(hours=window_hours)
        cohort = await deps.ch.query(
            _COHORT_SQL,
            parameters={
                "project_id": str(draft["project_id"]),
                "since": since,
                "limit": MAX_COHORT,
            },
        )

        if not cohort:
            await deps.pool.execute(
                """
                update backtest_run
                set status='done', cohort_size=0, item_total=0, item_done=0,
                    caught=0, missed=0, spans_scanned=0, cost_usd=0,
                    finished_at=now()
                where id=$1
                """,
                backtest_run_id,
            )
            await _mark_draft_ready(deps.pool, draft)
            return

        judge_kind = draft["judge_kind"]
        judge_config = draft["judge_config"] or {}

        rows: list[tuple[Any, ...]] = []
        cost_usd = 0.0
        item_done = 0
        caught = 0
        flagged_start_times: list[datetime] = []
        judged_at = datetime.now(UTC)
        capped = False
        cap_reason = ""

        for spans_scanned, cohort_run in enumerate(cohort, start=1):
            run_cost = float(cohort_run.get("cost_usd") or 0.0)
            cost_usd += run_cost

            if spans_scanned > MAX_SPANS:
                capped = True
                cap_reason = "cap_exceeded:spans_scanned"
                break
            if cost_usd > COST_CEILING_USD:
                capped = True
                cap_reason = "cap_exceeded:cost_usd"
                break

            score, label, rationale, raw_output, outcome = await _score_run(
                deps, judge_kind, judge_config, cohort_run, project_id=draft["project_id"]
            )

            rows.append(
                (
                    str(draft["project_id"]),
                    str(cohort_run["run_id"]),
                    None,  # span_id
                    str(draft["id"]),  # draft_id
                    judge_kind,  # judge_name
                    "builtin" if not judge_kind.startswith("luna:") else "luna",
                    "v1",  # judge_version
                    float(score),
                    label,
                    rationale,
                    raw_output,
                    outcome,
                    judged_at,
                    run_cost,
                )
            )
            item_done += 1
            if outcome == "ok" and score <= FLAG_THRESHOLD:
                caught += 1
                flagged_start_times.append(cohort_run["start_time"])

            await deps.pool.execute(
                """
                update backtest_run
                set heartbeat_at=now(), item_done=$2, spans_scanned=$3, cost_usd=$4
                where id=$1
                """,
                backtest_run_id,
                item_done,
                spans_scanned,
                cost_usd,
            )

        if rows:
            await deps.ch.insert(
                "backtest_score",
                rows,
                column_names=[
                    "project_id",
                    "run_id",
                    "span_id",
                    "draft_id",
                    "judge_name",
                    "judge_endpoint",
                    "judge_version",
                    "score",
                    "label",
                    "rationale",
                    "raw_output",
                    "outcome",
                    "judged_at",
                    "cost_usd",
                ],
            )

        if capped:
            await _mark_backtest_failed(deps.pool, backtest_run_id, cap_reason)
            return

        missed = item_done - caught
        would_have_flagged_at = min(flagged_start_times) if flagged_start_times else None

        await deps.pool.execute(
            """
            update backtest_run
            set status='done', caught=$2, missed=$3, would_have_flagged_at=$4,
                finished_at=now()
            where id=$1
            """,
            backtest_run_id,
            caught,
            missed,
            would_have_flagged_at,
        )
        await _mark_draft_ready(deps.pool, draft)
    except Exception as exc:  # noqa: BLE001
        await _mark_backtest_failed(deps.pool, backtest_run_id, str(exc))


async def _score_run(
    deps: VerbDeps,
    judge_kind: str,
    judge_config: dict[str, Any],
    run: dict[str, Any],
    *,
    project_id: UUID,
) -> tuple[float, str, str, str, str]:
    """Score one cohort run. Returns
    ``(score, label, rationale, raw_output, outcome)``.

    Deterministic kinds (``echo``, ``contains``) mirror ``evals.py``'s
    built-in judges exactly, applied against the run's ``outputs``
    field. ``luna:<slug>`` delegates to ``apply_luna_judge``. A judge
    error (bad config, malformed row, provider failure) never raises —
    it's captured as ``outcome='judge_unavailable'`` so the caller can
    keep the run going.
    """
    try:
        if judge_kind.startswith("luna:"):
            # A `luna:proposed` draft's `judge_config` is
            # `{"prompt", "threshold", "label"}` (see verbs/propose.py's
            # `ProposedJudge`) — NOT a `luna_judge` row shape.
            # `apply_luna_judge` expects a `judge_row`-shaped dict
            # (`rubric_prompt`/`provider`/`model`/...), so build one here
            # rather than passing `judge_config` straight through.
            judge_row = {
                "rubric_prompt": judge_config["prompt"],
                "provider": "anthropic",
                "model": "claude-3-5-haiku-latest",
                "output_format": "score-rationale",
                "temperature": 0.0,
                "max_tokens": 512,
                "slug": "proposed",
            }
            score, label, rationale, raw_output, _cost = await luna_judges.apply_luna_judge(
                judge_row,
                pool=deps.pool,
                project_id=project_id,
                surface="eval",
                surface_ref_id=run["run_id"],
                input_text=str(run["inputs"]),
                expected="",
                output_text=str(run["outputs"]),
            )
            outcome = "ok" if label != "error" else "judge_unavailable"
            return score, label, rationale, raw_output, outcome

        base_kind = judge_kind
        output_text = run["outputs"]
        expected = judge_config.get("expected", "")

        if base_kind == "echo":
            return 1.0, "pass", "echo: smoke-test, always 1.0", "", "ok"
        if base_kind == "exact":
            ok = output_text == expected
            return (
                1.0 if ok else 0.0,
                "pass" if ok else "fail",
                "exact match" if ok else "output != expected",
                "",
                "ok",
            )
        if base_kind == "contains":
            ok = bool(expected) and (expected in output_text)
            return (
                1.0 if ok else 0.0,
                "pass" if ok else "fail",
                "expected found in output" if ok else "expected not in output",
                "",
                "ok",
            )
        return 0.0, "fail", f"unknown judge kind: {judge_kind}", "", "judge_unavailable"
    except Exception as exc:  # noqa: BLE001
        return 0.0, "error", f"judge error: {exc}", "", "judge_unavailable"


async def _mark_backtest_failed(pool: Any, backtest_run_id: UUID, reason: str) -> None:
    await pool.execute(
        """
        update backtest_run
        set status='failed', error=$2, finished_at=now()
        where id=$1
        """,
        backtest_run_id,
        reason[:2000],
    )


async def _mark_draft_ready(pool: Any, draft: dict[str, Any]) -> None:
    """Transition a draft BACKTESTING -> READY on a successfully
    finished (``done``) backtest. Guarded by :func:`can_transition` so
    a draft already promoted/discarded/ready is left untouched — this
    is what makes ``promote_to_recurring``'s READY gate mean "has
    completed a backtest", not just "was drafted"."""
    current_status = DraftStatus(draft["status"])
    if not can_transition(current_status, DraftStatus.READY):
        return
    await pool.execute(
        """
        update backtest_draft
        set status = $2
        where id = $1
        """,
        draft["id"],
        DraftStatus.READY.value,
    )
