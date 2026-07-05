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

from .config import Settings, load
from .ticks.reaper import reap_once


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


async def _serve(settings: Settings) -> None:
    log = structlog.get_logger("langprobe.scheduler")
    pool = await asyncpg.create_pool(settings.pg_dsn, min_size=1, max_size=4)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(
        reaper_loop(
            pool,
            interval_s=settings.reaper_interval_s,
            lease_timeout_s=settings.lease_timeout_s,
        )
    )
    log.info("scheduler starting", reaper_interval_s=settings.reaper_interval_s)
    try:
        await stop.wait()
    finally:
        log.info("scheduler stopping")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await pool.close()


def main() -> None:
    settings = load()
    _configure_logging(settings.log_level)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
