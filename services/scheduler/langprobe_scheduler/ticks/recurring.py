"""Recurring-judge tick — closes the self-improving eval loop.

``promote_to_recurring`` stamps a ``luna_judge`` as recurring with a
cadence and a ``scored_through`` watermark (= promotion time, so scoring
starts forward, not over history the backtest already covered). This
tick is the thing that then actually re-runs the judge against new
traffic:

  1. find every judge that is due (``last_scored_at`` older than its
     ``schedule_seconds``),
  2. take a per-judge advisory lock so two replicas never score the same
     judge in the same tick (the lock is held across scoring, so the
     write-back is single-writer),
  3. pull only runs newer than the watermark (bounded to ``max_cohort``),
  4. score each through the SHARED ``apply_luna_judge`` path — the exact
     same resolver + gateway dispatch + parser the manual eval router
     uses, so recurring and manual scoring can never drift,
  5. write one ``eval_score`` row per run (ReplacingMergeTree keyed on
     ``(project_id, eval_config_id=judge_id, run_id, judge_name)`` so a
     re-score after a crash collapses instead of double-counting),
  6. advance the watermark to the newest run scored.

Scores land in the same ``eval_score`` store the alert evaluator reads,
so a recurring judge whose average crosses a threshold opens an incident
through the existing alert machinery — the loop's "watch it" step for
free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import structlog
from langprobe_api.routers.luna_judges import apply_luna_judge, resolve_judge
from langprobe_api.tenant_scope import resolve_tenant_ids

from langprobe_scheduler.locks import advisory_lock

log = structlog.get_logger("langprobe.scheduler.recurring")

# Judges whose last run is older than their cadence.
_DUE_SQL = """
    select id, project_id, slug, scored_through
      from luna_judge
     where is_recurring and recurring_enabled and deleted_at is null
       and (last_scored_at is null
            or last_scored_at < now() - make_interval(secs => schedule_seconds))
"""

# Re-checked under the lock: another replica may have scored (and thus
# un-due'd) this judge between the due-scan and our acquiring the lock.
_STILL_DUE_SQL = """
    select recurring_enabled and deleted_at is null
       and (last_scored_at is null
            or last_scored_at < now() - make_interval(secs => schedule_seconds))
      from luna_judge
     where id = $1
"""

# New traffic since the watermark, oldest-first + capped, so a backlog
# larger than max_cohort drains monotonically across successive ticks.
_NEW_RUNS_SQL = """
    select run_id, start_time, inputs, outputs
      from run final
     where project_id = {project_id:UUID}
       and start_time > {watermark:DateTime64(9)}
     order by start_time asc
     limit {limit:UInt32}
"""


class _TickBudget:
    """Soft per-tick dollar budget. Checked after each score, so a tick can
    overshoot by at most one score's cost — the goal is bounding a firehose,
    not exact accounting. cap <= 0 means unlimited."""

    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self.spent = 0.0

    def charge(self, usd: float) -> None:
        self.spent += usd

    def exhausted(self) -> bool:
        return self.cap > 0 and self.spent >= self.cap


_EVAL_SCORE_COLUMNS = [
    # Tenant columns first — post-0006 eval_score keys on (org_id, ...) and has
    # no DEFAULT for them, so an insert that omits them writes the zero-UUID
    # tenant. Mirror the manual eval path (routers/evals.py). See
    # tenant_scope.resolve_tenant_ids / test_property_tenant_columns_on_insert.
    "org_id",
    "workspace_id",
    "project_id",
    "run_id",
    "span_id",
    "eval_config_id",
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
]


async def evaluate_recurring_once(
    pool: asyncpg.Pool,
    clickhouse: Any,
    *,
    max_cohort: int,
    cost_cap_usd: float = 1.00,
    _apply=apply_luna_judge,
    _resolve=resolve_judge,
) -> int:
    """One recurring pass over all due judges. Returns judges scored.

    ``clickhouse`` is required to read runs and write scores; when it is
    None (scheduler started without LANGPROBE_CLICKHOUSE_URL) the tick is
    a no-op. ``_apply``/``_resolve`` are injectable for tests.

    ``cost_cap_usd`` is a soft dollar ceiling for the whole pass (one
    ``_TickBudget`` spans every judge scored in this tick) so a firehose
    project can't make a single tick unbounded. <= 0 disables the cap.
    """
    if clickhouse is None:
        return 0
    budget = _TickBudget(cost_cap_usd)
    due = await pool.fetch(_DUE_SQL)
    scored = 0
    for judge in due:
        if budget.exhausted():
            break  # tick budget spent; remaining judges wait for the next tick
        async with advisory_lock(pool, f"recurring-judge:{judge['id']}") as (conn, got):
            if not got:
                continue
            still_due = await conn.fetchval(_STILL_DUE_SQL, judge["id"])
            if not still_due:
                continue
            try:
                if await _score_judge(
                    pool, conn, clickhouse, judge, max_cohort, budget, _apply, _resolve
                ):
                    scored += 1
            except Exception as exc:  # noqa: BLE001 — one bad judge must not stall the rest
                log.warning(
                    "recurring judge scoring failed",
                    judge_id=str(judge["id"]),
                    error=str(exc),
                )
                await conn.execute(
                    "update luna_judge set last_scored_at = now(), last_score_error = $2 where id = $1",
                    judge["id"],
                    str(exc)[:500],
                )
    return scored


async def _score_judge(
    pool: asyncpg.Pool,
    conn: asyncpg.Connection,
    clickhouse: Any,
    judge: asyncpg.Record,
    max_cohort: int,
    budget: _TickBudget,
    apply,
    resolve,
) -> bool:
    """Score one due judge over its new runs. Returns True if it scored
    at least one run. Watermark/last_scored_at writes go through ``conn``
    (the lock-holding transaction); reads + dispatch use ``pool``."""
    judge_cfg = await resolve(pool, judge["project_id"], judge["slug"])
    if judge_cfg is None:
        # Deleted between due-scan and lock; just mark it seen.
        await _bump_seen(conn, judge["id"])
        return False

    watermark = judge["scored_through"] or datetime.now(UTC)
    runs = await clickhouse.query(
        _NEW_RUNS_SQL,
        parameters={
            "project_id": str(judge["project_id"]),
            "watermark": watermark,
            "limit": max_cohort,
        },
    )
    if not runs:
        await _bump_seen(conn, judge["id"])
        return False

    # Every eval_score insert must carry the tenant tuple (see _EVAL_SCORE_COLUMNS).
    org_id, workspace_id = await resolve_tenant_ids(pool, judge["project_id"])
    judged_at = datetime.now(UTC)
    rows: list[tuple[Any, ...]] = []
    new_watermark = watermark
    for run in runs:
        score, label, rationale, raw_output, cost_usd = await apply(
            judge_cfg,
            pool=pool,
            project_id=judge["project_id"],
            surface="luna",
            surface_ref_id=judge["id"],
            input_text=run["inputs"] or "",
            expected="",
            output_text=run["outputs"] or "",
        )
        budget.charge(cost_usd)
        outcome = "judge_unavailable" if label == "error" else "ok"
        rows.append(
            (
                str(org_id),
                str(workspace_id),
                str(judge["project_id"]),
                str(run["run_id"]),
                None,  # span_id — recurring judges score at the run grain
                str(judge["id"]),  # eval_config_id: judge id → per-(judge,run) dedup key
                f"luna:{judge['slug']}",  # judge_name (how luna scores are tagged everywhere)
                "luna",  # judge_endpoint
                "recurring",  # judge_version — distinguishes from manual eval runs
                float(score),
                label,
                rationale,
                raw_output,
                outcome,
                judged_at,
                cost_usd,  # real per-call cost (was hardcoded 0)
            )
        )
        run_start = _as_utc(run["start_time"])
        if run_start > new_watermark:
            new_watermark = run_start
        if budget.exhausted():
            # This call's cost pushed the tick over budget. It's already
            # recorded + watermarked (append-then-check), so the next tick
            # won't re-score (and re-pay for) it — the tick just overshoots
            # by at most one score's cost, which is the intended tradeoff.
            break

    await clickhouse.insert("eval_score", rows, column_names=_EVAL_SCORE_COLUMNS)
    await conn.execute(
        """
        update luna_judge
           set scored_through = $2, last_scored_at = now(), last_score_error = null
         where id = $1
        """,
        judge["id"],
        new_watermark,
    )
    log.info("scored recurring judge", judge_id=str(judge["id"]), runs=len(rows))
    return True


async def _bump_seen(conn: asyncpg.Connection, judge_id: Any) -> None:
    """No new runs (or judge gone): record the tick so it isn't due again
    until its cadence elapses, without moving the watermark."""
    await conn.execute(
        "update luna_judge set last_scored_at = now(), last_score_error = null where id = $1",
        judge_id,
    )


def _as_utc(value: datetime) -> datetime:
    """ClickHouse may hand back naive datetimes; the watermark column is
    timestamptz, so normalize to tz-aware UTC before comparing/writing."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
