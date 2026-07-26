"""Alert rule evaluation — measure a metric, open/resolve incidents.

Extracted from ``routers/alerts.py`` so the scheduler service can run it
without importing FastAPI. Each per-rule decision takes a transaction-scoped
Postgres advisory lock keyed on the rule id, so two scheduler replicas (or a
lingering API pod mid-deploy) can never both open an incident for the same
rule. The lock auto-releases on commit/connection-death.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog

from ..clickhouse_client import ClickHouseQuery

log = structlog.get_logger("langprobe.api.alerts.evaluator")


async def evaluate_due_rules(pool: asyncpg.Pool, clickhouse: ClickHouseQuery | None) -> None:
    """Single tick over all enabled rules. Public for the scheduler + tests."""
    if clickhouse is None:
        return
    rules = await pool.fetch(
        """
        select id, project_id, metric, comparator, threshold,
               window_seconds, open_incident_id
          from alert_rule
         where enabled
        """
    )
    for rule in rules:
        try:
            value = await _measure(clickhouse, rule)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "alert measure failed",
                rule_id=str(rule["id"]),
                metric=rule["metric"],
                error=str(exc),
            )
            continue
        await _apply_rule_decision(pool, rule, value)


async def _measure(clickhouse: ClickHouseQuery, rule: asyncpg.Record) -> float | None:
    metric: str = rule["metric"]
    project_id: UUID = rule["project_id"]
    window: int = rule["window_seconds"]
    params = {"project_id": str(project_id), "window": window}

    if metric == "error_rate":
        sql = """
            select
                count() as runs,
                countIf(status = 'error') as errors
              from run final
             where project_id = {project_id:UUID}
               and start_time >= now64(9) - toIntervalSecond({window:UInt32})
        """
        rows = await clickhouse.query(sql, parameters=params)
        if not rows:
            return None
        runs = int(rows[0].get("runs", 0) or 0)
        errors = int(rows[0].get("errors", 0) or 0)
        if runs == 0:
            return None
        return errors / runs

    if metric == "latency_p95_ms":
        sql = """
            select
                quantileTDigest(0.95)(toFloat64(duration_ns) / 1e6) as p95_ms
              from run final
             where project_id = {project_id:UUID}
               and start_time >= now64(9) - toIntervalSecond({window:UInt32})
        """
        rows = await clickhouse.query(sql, parameters=params)
        if not rows:
            return None
        return _opt_float(rows[0].get("p95_ms"))

    if metric == "runs_per_min":
        sql = """
            select count() as runs
              from run final
             where project_id = {project_id:UUID}
               and start_time >= now64(9) - toIntervalSecond({window:UInt32})
        """
        rows = await clickhouse.query(sql, parameters=params)
        if not rows:
            return None
        runs = int(rows[0].get("runs", 0) or 0)
        return runs / max(window / 60.0, 1.0)

    if metric == "cost_usd":
        sql = """
            select toFloat64(sum(cost_usd)) as total
              from run final
             where project_id = {project_id:UUID}
               and start_time >= now64(9) - toIntervalSecond({window:UInt32})
        """
        rows = await clickhouse.query(sql, parameters=params)
        if not rows:
            return None
        return float(rows[0].get("total", 0) or 0.0)

    return None


async def _apply_rule_decision(
    pool: asyncpg.Pool, rule: asyncpg.Record, value: float | None
) -> None:
    rule_id: UUID = rule["id"]
    project_id: UUID = rule["project_id"]
    threshold: float = float(rule["threshold"])
    comparator: str = rule["comparator"]

    breaches = value is not None and _compare(value, comparator, threshold)

    async with pool.acquire() as conn, conn.transaction():
        # Per-rule advisory lock: whoever loses the race skips this rule this
        # tick; the winner holds the lock until this transaction commits.
        got = await conn.fetchval(
            "select pg_try_advisory_xact_lock(hashtextextended($1, 0))",
            f"alert-rule:{rule_id}",
        )
        if not got:
            return

        # Re-read the open-incident pointer *under the lock*. ``rule`` carries the
        # value from the pre-lock scan; try-lock losers skip rather than wait, so
        # two staggered replicas can both reach this rule with a stale NULL and
        # both open an incident (the exact double-fire this lock exists to stop)
        # unless the decision is made on the freshly-read pointer.
        open_event_id: UUID | None = await conn.fetchval(
            "select open_incident_id from alert_rule where id = $1", rule_id
        )

        await conn.execute(
            """
                update alert_rule
                   set last_evaluated_at = now(),
                       last_value = $2
                 where id = $1
                """,
            rule_id,
            value,
        )

        if breaches and open_event_id is None:
            # Open a fresh incident.
            incident_id = uuid4()
            event_id = await conn.fetchval(
                """
                    insert into alert_event (
                        rule_id, project_id, kind, value, threshold, incident_id
                    )
                    values ($1, $2, 'fired', $3, $4, $5)
                    returning id
                    """,
                rule_id,
                project_id,
                value,
                threshold,
                incident_id,
            )
            await conn.execute(
                "update alert_rule set open_incident_id = $1 where id = $2",
                event_id,
                rule_id,
            )
        elif not breaches and open_event_id is not None:
            # Resolve the open incident -- reuse its incident_id.
            incident_id = await conn.fetchval(
                "select incident_id from alert_event where id = $1",
                open_event_id,
            )
            if incident_id is None:
                # Open pointer is stale; clear it and move on.
                await conn.execute(
                    "update alert_rule set open_incident_id = null where id = $1",
                    rule_id,
                )
                return
            await conn.execute(
                """
                    insert into alert_event (
                        rule_id, project_id, kind, value, threshold, incident_id
                    )
                    values ($1, $2, 'resolved', $3, $4, $5)
                    """,
                rule_id,
                project_id,
                value if value is not None else 0.0,
                threshold,
                incident_id,
            )
            await conn.execute(
                "update alert_rule set open_incident_id = null where id = $1",
                rule_id,
            )


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == ">":
        return value > threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    if comparator == "<=":
        return value <= threshold
    return False


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f
