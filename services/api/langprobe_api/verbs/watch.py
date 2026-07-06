"""watch_judge verb (Task 6, D2-A) — status polling for a backtest run.

An agent driving the eval loop (cluster -> propose -> backtest ->
promote) polls this verb for a ``backtest_run``'s status instead of
blocking on the executor. ``backtest_run`` has no ``project_id`` column
of its own (it only carries ``draft_id``), so scope is resolved by
joining through its parent ``backtest_draft``.

STALE-HEARTBEAT TERMINATION: the executor (``verbs/backtest.py``'s
``_run_backtest``) updates ``heartbeat_at`` on every cohort item while
``status='running'``. If the process running that loop is killed
(pod restart, OOM, deploy) mid-run, the row is left stuck at
``running`` with no writer left to ever move it to a terminal state.
The durable reaper in ``services/scheduler`` (backtest-reaper tick) is
the primary mechanism that terminates such orphans, on a fixed cadence,
whether or not anyone polls. ``watch_judge`` keeps a best-effort
fast-path: if a polled run is ``running`` with a heartbeat older than
``LEASE_TIMEOUT_S`` it flips it to ``failed`` here too, so a driving
agent's poll loop terminates immediately instead of waiting for the next
reaper tick. The UPDATE is guarded by ``and status='running'`` so this
flip and the reaper's flip can never double-write a terminal state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import WatchIn, WatchOut
from langprobe_api.verbs.scope import ScopeError, require_project_scope

# A running backtest_run whose heartbeat_at is older than this is
# considered orphaned (its executor process is gone) and is declared
# failed on the next watch_judge poll.
LEASE_TIMEOUT_S = 120


async def watch_judge(deps: VerbDeps, ctx: TenantContext, params: WatchIn) -> WatchOut:
    run = await deps.pool.fetchrow(
        """
        select br.id, br.status, br.caught, br.missed, br.error,
               br.heartbeat_at, bd.project_id as project_id
        from backtest_run br
        join backtest_draft bd on bd.id = br.draft_id
        where br.id = $1
        """,
        params.target_id,
    )
    if run is None:
        raise ScopeError(f"backtest_run {params.target_id} not found")
    require_project_scope(ctx, run["project_id"])

    if run["status"] == "running" and _heartbeat_is_stale(run["heartbeat_at"]):
        await deps.pool.execute(
            """
            update backtest_run
            set status = 'failed', error = $2, finished_at = now()
            where id = $1 and status = 'running'
            """,
            run["id"],
            "heartbeat_timeout",
        )
        return WatchOut(
            status="failed",
            caught=run["caught"],
            missed=run["missed"],
            error="heartbeat_timeout",
        )

    return WatchOut(
        status=run["status"],
        caught=run["caught"],
        missed=run["missed"],
        error=run["error"],
    )


def _heartbeat_is_stale(heartbeat_at: datetime | None) -> bool:
    """A missing heartbeat (never set, e.g. still queued-then-stuck) is
    treated as stale too — there is nothing to prove the run is still
    alive."""
    if heartbeat_at is None:
        return True
    return heartbeat_at < datetime.now(UTC) - timedelta(seconds=LEASE_TIMEOUT_S)
