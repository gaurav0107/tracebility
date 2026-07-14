"""Backtest reaper tick.

A ``backtest_run`` executor (services/api verbs/backtest.py) updates
``heartbeat_at`` on every cohort item while ``status='running'``. If that
process dies mid-run (pod restart, OOM, deploy), the row is stranded at
``running`` with no writer to ever move it to a terminal state. Before
this service, ``watch_judge`` reaped such rows in-band, but only if an
agent happened to poll. This tick reaps them durably regardless.

Correctness rests on the guarded UPDATE (``and status='running'``): it is
atomic and idempotent, so a concurrent ``watch_judge`` flip or a second
reaper replica is a harmless no-op. The advisory lock additionally keeps
two replicas from redundantly scanning the same row each tick, and is the
shared primitive the later recurring-judge and alert ticks (which are NOT
idempotent) will depend on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import structlog

from langprobe_scheduler.locks import advisory_lock

log = structlog.get_logger("langprobe.scheduler.reaper")

_REAP_SQL = """
    update backtest_run
       set status = 'failed', error = 'heartbeat_timeout', finished_at = now()
     where id = $1 and status = 'running'
"""


async def reap_once(pool: asyncpg.Pool, *, lease_timeout_s: int) -> int:
    """Flip every orphaned running backtest_run to failed. Returns count."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_timeout_s)
    stale = await pool.fetch(
        """
        select id from backtest_run
         where status = 'running'
           and (heartbeat_at is null or heartbeat_at < $1)
        """,
        cutoff,
    )
    reaped = 0
    for row in stale:
        async with advisory_lock(pool, f"backtest-reaper:{row['id']}") as (conn, got):
            if not got:
                continue
            result = await conn.execute(_REAP_SQL, row["id"])
            if result == "UPDATE 1":
                reaped += 1
                log.info("reaped orphaned backtest run", run_id=str(row["id"]))
    return reaped
