"""Alert rules + incidents (parity loop #4 item #6).

Single self-hosted alerting engine. A rule pins one metric on one
project to a threshold over a sliding window. The evaluator (see
``langprobe_api.alerts.evaluator``) re-runs the same ClickHouse query the
Monitoring page uses, compares the value, and either opens a fresh
incident or resolves an existing one. Both transitions write a row to
`alert_event` so history is a flat scan, not a derived view.

Why no separate `incident` table? An incident is just a pair of events
(fired -> resolved) joined by `incident_id`. Materializing it as a
third table buys nothing while v1 still routes nowhere. When delivery
channels land we can collapse the schema then.

V1 honest scope:
- Metrics: error_rate, latency_p95_ms, runs_per_min, cost_usd.
- Comparators: > >= < <=.
- Routes are stored but not delivered. UI surfaces the queue + history
  so the loop ships value before Slack/PagerDuty integration.
- Evaluator runs out-of-process in the scheduler service; one tick = one
  pass over enabled rules across all projects. Bounded by
  `window_seconds` (60-86400) so a misconfigured rule can't ask
  ClickHouse for a year.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..alerts.evaluator import _opt_float
from ..auth import Principal, assert_workspace_role, require_user

log = structlog.get_logger("langprobe.api.alerts")

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])

_METRICS = {"error_rate", "latency_p95_ms", "runs_per_min", "cost_usd"}
_COMPARATORS = {">", ">=", "<", "<="}
_ROUTE_KINDS = {"slack", "pagerduty", "webhook", "email"}


class AlertRoute(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    target: str = Field(min_length=1, max_length=512)


class AlertRuleOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    metric: str
    comparator: str
    threshold: float
    window_seconds: int
    routes: list[AlertRoute]
    enabled: bool
    last_evaluated_at: datetime | None
    last_value: float | None
    open_incident_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AlertRuleCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    metric: str
    comparator: str
    threshold: float
    window_seconds: int = Field(ge=60, le=86400)
    routes: list[AlertRoute] = Field(default_factory=list)
    enabled: bool = True


class AlertRulePatch(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    metric: str | None = None
    comparator: str | None = None
    threshold: float | None = None
    window_seconds: int | None = Field(default=None, ge=60, le=86400)
    routes: list[AlertRoute] | None = None
    enabled: bool | None = None


class AlertEventOut(BaseModel):
    id: UUID
    rule_id: UUID
    rule_name: str | None
    project_id: UUID
    kind: str
    value: float
    threshold: float
    occurred_at: datetime
    incident_id: UUID


class AlertEventList(BaseModel):
    events: list[AlertEventOut]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AlertRuleOut])
async def list_rules(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[AlertRuleOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, name, metric, comparator, threshold,
               window_seconds, routes, enabled, last_evaluated_at,
               last_value, open_incident_id, created_at, updated_at
          from alert_rule
         where project_id = $1
         order by created_at desc
        """,
        project_id,
    )
    return [_rule_out(r) for r in rows]


@router.post("", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    request: Request,
    body: AlertRuleCreate,
    principal: Principal = Depends(require_user),
) -> AlertRuleOut:
    if body.metric not in _METRICS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"metric must be one of {sorted(_METRICS)}",
        )
    if body.comparator not in _COMPARATORS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"comparator must be one of {sorted(_COMPARATORS)}",
        )
    for route in body.routes:
        if route.kind not in _ROUTE_KINDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"route kind must be one of {sorted(_ROUTE_KINDS)}",
            )

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    routes_json = _json.dumps([r.model_dump() for r in body.routes])
    row = await pool.fetchrow(
        """
        insert into alert_rule (
            project_id, name, metric, comparator, threshold,
            window_seconds, routes, enabled, created_by
        )
        values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
        returning id, project_id, name, metric, comparator, threshold,
                  window_seconds, routes, enabled, last_evaluated_at,
                  last_value, open_incident_id, created_at, updated_at
        """,
        body.project_id,
        body.name,
        body.metric,
        body.comparator,
        body.threshold,
        body.window_seconds,
        routes_json,
        body.enabled,
        principal.user_id,
    )
    assert row is not None
    await audit.record(
        pool,
        principal=principal,
        action="alert_rule.create",
        target_kind="alert_rule",
        target_id=row["id"],
        payload={
            "metric": body.metric,
            "comparator": body.comparator,
            "threshold": body.threshold,
            "window_seconds": body.window_seconds,
            "enabled": body.enabled,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return _rule_out(row)


@router.patch("/{rule_id}", response_model=AlertRuleOut)
async def patch_rule(
    request: Request,
    rule_id: UUID,
    body: AlertRulePatch,
    principal: Principal = Depends(require_user),
) -> AlertRuleOut:
    pool: asyncpg.Pool = request.app.state.pg
    existing = await pool.fetchrow("select project_id from alert_rule where id = $1", rule_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert rule not found")
    project_id: UUID = existing["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )

    if body.metric is not None and body.metric not in _METRICS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid metric")
    if body.comparator is not None and body.comparator not in _COMPARATORS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid comparator")
    if body.routes is not None:
        for route in body.routes:
            if route.kind not in _ROUTE_KINDS:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid route kind")

    sets: list[str] = []
    args: list[Any] = []
    idx = 1

    def _add(col: str, value: Any, *, cast: str | None = None) -> None:
        nonlocal idx
        placeholder = f"${idx}"
        if cast:
            placeholder = f"${idx}::{cast}"
        sets.append(f"{col} = {placeholder}")
        args.append(value)
        idx += 1

    if body.name is not None:
        _add("name", body.name)
    if body.metric is not None:
        _add("metric", body.metric)
    if body.comparator is not None:
        _add("comparator", body.comparator)
    if body.threshold is not None:
        _add("threshold", body.threshold)
    if body.window_seconds is not None:
        _add("window_seconds", body.window_seconds)
    if body.routes is not None:
        _add(
            "routes",
            _json.dumps([r.model_dump() for r in body.routes]),
            cast="jsonb",
        )
    if body.enabled is not None:
        _add("enabled", body.enabled)

    if not sets:
        row = await pool.fetchrow(
            """select id, project_id, name, metric, comparator, threshold,
                      window_seconds, routes, enabled, last_evaluated_at,
                      last_value, open_incident_id, created_at, updated_at
                 from alert_rule where id = $1""",
            rule_id,
        )
        assert row is not None
        return _rule_out(row)

    args.append(rule_id)
    sql = (
        "update alert_rule set "
        + ", ".join(sets)
        + f" where id = ${idx} "
        + "returning id, project_id, name, metric, comparator, threshold, "
        "window_seconds, routes, enabled, last_evaluated_at, last_value, "
        "open_incident_id, created_at, updated_at"
    )
    row = await pool.fetchrow(sql, *args)
    assert row is not None
    await audit.record(
        pool,
        principal=principal,
        action="alert_rule.update",
        target_kind="alert_rule",
        target_id=rule_id,
        payload=body.model_dump(exclude_none=True),
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _rule_out(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    request: Request,
    rule_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    existing = await pool.fetchrow("select project_id from alert_rule where id = $1", rule_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert rule not found")
    project_id: UUID = existing["project_id"]
    workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    await pool.execute("delete from alert_rule where id = $1", rule_id)
    await audit.record(
        pool,
        principal=principal,
        action="alert_rule.delete",
        target_kind="alert_rule",
        target_id=rule_id,
        payload={},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )


@router.get("/events", response_model=AlertEventList)
async def list_events(
    request: Request,
    project_id: UUID = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_user),
) -> AlertEventList:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(pool, principal, project_id, ("owner", "admin", "member", "viewer"))
    rows = await pool.fetch(
        """
        select e.id, e.rule_id, r.name as rule_name, e.project_id,
               e.kind, e.value, e.threshold, e.occurred_at, e.incident_id
          from alert_event e
          left join alert_rule r on r.id = e.rule_id
         where e.project_id = $1
         order by e.occurred_at desc
         limit $2
        """,
        project_id,
        limit,
    )
    return AlertEventList(
        events=[
            AlertEventOut(
                id=r["id"],
                rule_id=r["rule_id"],
                rule_name=r["rule_name"],
                project_id=r["project_id"],
                kind=r["kind"],
                value=float(r["value"]),
                threshold=float(r["threshold"]),
                occurred_at=r["occurred_at"],
                incident_id=r["incident_id"],
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _assert_project_role(
    pool: asyncpg.Pool,
    principal: Principal,
    project_id: UUID,
    allowed: tuple[str, ...],
) -> UUID:
    workspace_id = await pool.fetchval(
        "select workspace_id from project where id = $1 and deleted_at is null",
        project_id,
    )
    if workspace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=allowed,
    )
    return workspace_id


def _rule_out(row: asyncpg.Record) -> AlertRuleOut:
    routes_raw = row["routes"]
    if isinstance(routes_raw, str):
        try:
            routes_list = _json.loads(routes_raw)
        except _json.JSONDecodeError:
            routes_list = []
    else:
        routes_list = routes_raw or []
    routes = [
        AlertRoute(kind=r.get("kind", ""), target=r.get("target", ""))
        for r in routes_list
        if isinstance(r, dict)
    ]
    return AlertRuleOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        metric=row["metric"],
        comparator=row["comparator"],
        threshold=float(row["threshold"]),
        window_seconds=int(row["window_seconds"]),
        routes=routes,
        enabled=bool(row["enabled"]),
        last_evaluated_at=row["last_evaluated_at"],
        last_value=_opt_float(row["last_value"]),
        open_incident_id=row["open_incident_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
