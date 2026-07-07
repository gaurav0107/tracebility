"""Scheduler configuration.

Reads env into a frozen Settings. The Postgres DSN uses the same env
name (LANGPROBE_PG_DSN) the api and migrator use, not the worker's
LANGPROBE_PG_URL — the scheduler only touches the control plane.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pg_dsn: str
    reaper_interval_s: int = 60
    lease_timeout_s: int = 120
    log_level: str = "INFO"
    clickhouse_url: str | None = None
    alert_interval_s: int = 60


def load() -> Settings:
    pg_dsn = os.environ.get("LANGPROBE_PG_DSN")
    if not pg_dsn:
        raise RuntimeError("LANGPROBE_PG_DSN is required")
    return Settings(
        pg_dsn=pg_dsn,
        reaper_interval_s=int(os.environ.get("LANGPROBE_SCHEDULER_REAPER_INTERVAL_S", "60")),
        lease_timeout_s=int(os.environ.get("LANGPROBE_SCHEDULER_LEASE_TIMEOUT_S", "120")),
        log_level=os.environ.get("LANGPROBE_LOG_LEVEL", "INFO"),
        clickhouse_url=os.environ.get("LANGPROBE_CLICKHOUSE_URL"),
        alert_interval_s=int(os.environ.get("LANGPROBE_SCHEDULER_ALERT_INTERVAL_S", "60")),
    )
