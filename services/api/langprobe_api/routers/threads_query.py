"""Threads — multi-turn session rollup.

A "thread" is the set of runs in a project that share a non-empty
`session_id`. The list view groups by session_id and aggregates the
fields you actually care about when triaging an agent conversation:
turn count, total cost, error count, latency p95, last activity.

Why not a materialized view yet: at MVP volume, a single GROUP BY
against the `run` table is fast enough and avoids a second source of
truth. We'll cut over to a `thread_summary` matview when scans get
expensive.

Failure modes:
- ClickHouse not configured: 503 ("data plane not configured"), not
  a silent empty list — the operator needs to see why.
- session_id is empty for single-turn calls; those are intentionally
  excluded from the thread list (they are visible on /runs).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery

log = structlog.get_logger("langprobe.api.threads_query")

router = APIRouter(prefix="/v1/threads", tags=["threads"])


class ThreadListItem(BaseModel):
    session_id: str
    end_user_id: str | None
    turn_count: int
    first_run_at: datetime
    last_run_at: datetime
    total_cost_usd: float
    total_tokens: int
    error_count: int
    latency_p95_ms: float | None
    last_run_id: UUID
    last_status: str


class ThreadListResponse(BaseModel):
    items: list[ThreadListItem]


class ThreadRun(BaseModel):
    run_id: UUID
    name: str
    kind: str
    status: str
    start_time: datetime
    end_time: datetime | None
    latency_ms: float | None
    total_tokens: int
    cost_usd: float
    # Conversation view: the run's boundary I/O, truncated server-side
    # so a long session stays a cheap payload. Full I/O lives on
    # /v1/runs/{id}.
    inputs: str | None = None
    outputs: str | None = None
    error_message: str | None = None


class ThreadDetail(BaseModel):
    session_id: str
    project_id: UUID
    end_user_id: str | None
    turn_count: int
    first_run_at: datetime
    last_run_at: datetime
    total_cost_usd: float
    total_tokens: int
    error_count: int
    runs: list[ThreadRun]


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    request: Request,
    project_id: UUID = Query(...),
    end_user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_user),
) -> ThreadListResponse:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_access(pool, project_id, principal)
    ch = _require_clickhouse(request)

    # quantile(0.95) on duration_ns rolled up across the session;
    # argMax(run_id, start_time) and argMax(status, start_time) give
    # us the most recent run + its terminal status for the badge.
    # argMax(end_user_id, start_time) surfaces the end user of the most
    # recent turn (a session should be one end user, but we don't enforce it).
    end_user_filter = ""
    if end_user_id is not None:
        end_user_filter = "and end_user_id = {end_user_id:String}"
    sql = f"""
        select
            session_id,
            argMax(end_user_id, start_time) as end_user_id,
            count() as turn_count,
            min(start_time) as first_run_at,
            max(start_time) as last_run_at,
            sum(cost_usd) as total_cost_usd,
            sum(total_tokens) as total_tokens,
            countIf(status = 'error') as error_count,
            quantile(0.95)(duration_ns) as latency_p95_ns,
            argMax(run_id, start_time) as last_run_id,
            argMax(status, start_time) as last_status
        from run final
        where project_id = {{project_id:UUID}}
          and session_id != ''
          and session_id is not null
          {end_user_filter}
        group by session_id
        order by last_run_at desc
        limit {{limit:UInt32}} offset {{offset:UInt32}}
    """
    params: dict[str, object] = {
        "project_id": str(project_id),
        "limit": limit,
        "offset": offset,
    }
    if end_user_id is not None:
        params["end_user_id"] = end_user_id

    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("threads list query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items = [
        ThreadListItem(
            session_id=row["session_id"],
            end_user_id=row.get("end_user_id") or None,
            turn_count=int(row["turn_count"] or 0),
            first_run_at=row["first_run_at"],
            last_run_at=row["last_run_at"],
            total_cost_usd=float(row["total_cost_usd"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            error_count=int(row["error_count"] or 0),
            latency_p95_ms=_latency_ms(row.get("latency_p95_ns")),
            last_run_id=row["last_run_id"],
            last_status=row["last_status"],
        )
        for row in rows
    ]
    return ThreadListResponse(items=items)


@router.get("/{session_id}", response_model=ThreadDetail)
async def get_thread(
    request: Request,
    session_id: str,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> ThreadDetail:
    if not session_id or session_id == "-":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id is required")

    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_access(pool, project_id, principal)
    ch = _require_clickhouse(request)

    sql = """
        select run_id, name, kind, status, start_time, end_time,
               duration_ns, total_tokens, cost_usd, end_user_id,
               leftUTF8(inputs, 2000) as inputs,
               leftUTF8(outputs, 2000) as outputs,
               error_message
        from run final
        where project_id = {project_id:UUID}
          and session_id = {session_id:String}
        order by start_time asc
        limit 1000
    """
    params = {"project_id": str(project_id), "session_id": session_id}
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("thread detail query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found")

    runs = [
        ThreadRun(
            run_id=row["run_id"],
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            start_time=row["start_time"],
            end_time=row.get("end_time"),
            latency_ms=_latency_ms(row.get("duration_ns")),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_usd=float(row.get("cost_usd") or 0),
            inputs=row.get("inputs") or None,
            outputs=row.get("outputs") or None,
            error_message=row.get("error_message") or None,
        )
        for row in rows
    ]
    total_cost = sum(r.cost_usd for r in runs)
    total_tokens = sum(r.total_tokens for r in runs)
    error_count = sum(1 for r in runs if r.status == "error")
    # Thread-level end user: the most recent turn's end_user_id (rows are
    # start_time asc). A session should map to one end user, but we surface
    # the latest rather than assume uniqueness.
    end_user_id = next(
        (row.get("end_user_id") for row in reversed(rows) if row.get("end_user_id")),
        None,
    )
    return ThreadDetail(
        session_id=session_id,
        project_id=project_id,
        end_user_id=end_user_id,
        turn_count=len(runs),
        first_run_at=runs[0].start_time,
        last_run_at=runs[-1].start_time,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        error_count=error_count,
        runs=runs,
    )


async def _assert_project_access(
    pool: asyncpg.Pool, project_id: UUID, principal: Principal
) -> None:
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
        allowed=("owner", "admin", "member", "viewer"),
    )


def _require_clickhouse(request: Request) -> ClickHouseQuery:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ch


def _latency_ms(duration_ns: object) -> float | None:
    if duration_ns is None:
        return None
    try:
        return round(float(duration_ns) / 1_000_000, 3)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
