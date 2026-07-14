"""Scheduler entrypoint.

Builds the asyncpg pool and runs the periodic ticks until SIGTERM/SIGINT.
Phase 1 runs a single tick: the backtest reaper.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import asyncpg
import structlog
from langprobe_api.clickhouse_client import ClickHouseQuery

from .config import Settings, load
from .ticks.alerts import evaluate_alerts_once
from .ticks.reaper import reap_once
from .ticks.recurring import evaluate_recurring_once


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


async def reaper_loop(
    pool: asyncpg.Pool,
    *,
    interval_s: int,
    lease_timeout_s: int,
    _reap=reap_once,
) -> None:
    """Periodic backtest-reaper tick. Injectable ``_reap`` for tests."""
    log = structlog.get_logger("langprobe.scheduler.reaper")
    log.info("reaper loop starting", interval_s=interval_s, lease_timeout_s=lease_timeout_s)
    while True:
        try:
            reaped = await _reap(pool, lease_timeout_s=lease_timeout_s)
            if reaped:
                log.info("reaper tick done", reaped=reaped)
        except asyncio.CancelledError:
            log.info("reaper loop stopping")
            raise
        except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
            log.warning("reaper tick failed", error=str(exc))
        await asyncio.sleep(interval_s)


async def alert_loop(
    pool: asyncpg.Pool,
    clickhouse,
    *,
    interval_s: int,
    _eval=evaluate_alerts_once,
) -> None:
    """Periodic alert-evaluator tick. Injectable ``_eval`` for tests."""
    log = structlog.get_logger("langprobe.scheduler.alerts")
    log.info("alert loop starting", interval_s=interval_s)
    while True:
        try:
            await _eval(pool, clickhouse)
        except asyncio.CancelledError:
            log.info("alert loop stopping")
            raise
        except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
            log.warning("alert tick failed", error=str(exc))
        await asyncio.sleep(interval_s)


async def recurring_loop(
    pool: asyncpg.Pool,
    clickhouse,
    *,
    interval_s: int,
    max_cohort: int,
    cost_cap_usd: float,
    _eval=evaluate_recurring_once,
) -> None:
    """Periodic recurring-judge tick. Injectable ``_eval`` for tests."""
    log = structlog.get_logger("langprobe.scheduler.recurring")
    log.info("recurring loop starting", interval_s=interval_s, max_cohort=max_cohort)
    while True:
        try:
            scored = await _eval(pool, clickhouse, max_cohort=max_cohort, cost_cap_usd=cost_cap_usd)
            if scored:
                log.info("recurring tick done", judges_scored=scored)
        except asyncio.CancelledError:
            log.info("recurring loop stopping")
            raise
        except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
            log.warning("recurring tick failed", error=str(exc))
        await asyncio.sleep(interval_s)


async def _serve(settings: Settings) -> None:
    log = structlog.get_logger("langprobe.scheduler")
    pool = await asyncpg.create_pool(settings.pg_dsn, min_size=1, max_size=4)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    clickhouse = ClickHouseQuery(settings.clickhouse_url) if settings.clickhouse_url else None

    tasks = [
        asyncio.create_task(
            reaper_loop(
                pool,
                interval_s=settings.reaper_interval_s,
                lease_timeout_s=settings.lease_timeout_s,
            )
        ),
        asyncio.create_task(
            alert_loop(
                pool,
                clickhouse,
                interval_s=settings.alert_interval_s,
            )
        ),
        asyncio.create_task(
            recurring_loop(
                pool,
                clickhouse,
                interval_s=settings.recurring_interval_s,
                max_cohort=settings.recurring_max_cohort,
                cost_cap_usd=settings.recurring_tick_cost_cap_usd,
            )
        ),
    ]
    log.info(
        "scheduler starting",
        reaper_interval_s=settings.reaper_interval_s,
        alert_interval_s=settings.alert_interval_s,
        recurring_interval_s=settings.recurring_interval_s,
        clickhouse=bool(settings.clickhouse_url),
    )
    try:
        await stop.wait()
    finally:
        log.info("scheduler stopping")
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if clickhouse is not None:
            clickhouse.close()
        await pool.close()


def main() -> None:
    settings = load()
    _configure_logging(settings.log_level)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
