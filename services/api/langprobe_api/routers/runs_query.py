"""Read-side runs list, detail, and span tree.

Backs the web overview table and the run-detail page. Tenancy is
enforced by re-checking the caller has any role in the workspace
owning that project.

Why thin endpoints: we want the SDK quickstart loop to close as fast
as possible. We deliberately don't paginate-by-time yet on list; offset
is fine until the dataset is large enough to make scans painful.

Failure modes:
- ClickHouse not configured (LANGPROBE_CLICKHOUSE_URL unset): 503,
  not "empty list". Empty list would lie to the operator.
- ClickHouse unreachable: 503 with the underlying error class in logs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from ..auth import Principal, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import ScopedClickHouse, resolve_project_scope

log = structlog.get_logger("langprobe.api.runs_query")

router = APIRouter(prefix="/v1/runs", tags=["runs"])


class RunListItem(BaseModel):
    run_id: UUID
    name: str
    kind: str
    status: str
    start_time: datetime
    latency_ms: float | None
    total_tokens: int
    cost_usd: float
    sdk: str


class RunListResponse(BaseModel):
    items: list[RunListItem]


class RunDetail(BaseModel):
    run_id: UUID
    project_id: UUID
    parent_run_id: UUID | None
    name: str
    kind: str
    status: str
    start_time: datetime
    end_time: datetime | None
    latency_ms: float | None
    inputs: str
    outputs: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    sdk: str
    sdk_version: str
    session_id: str | None
    user_id: str | None
    tags: list[str]
    metadata: str
    error_kind: str
    error_message: str


class SpanItem(BaseModel):
    span_id: UUID
    parent_span_id: UUID | None
    name: str
    kind: str
    status: str
    start_time: datetime
    end_time: datetime | None
    latency_ms: float | None
    inputs: str
    outputs: str
    model: str
    temperature: float | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    error_kind: str
    error_message: str
    attributes: str


class SpanListResponse(BaseModel):
    items: list[SpanItem]


@router.get("", response_model=RunListResponse)
async def list_runs(
    request: Request,
    project_id: UUID = Query(...),
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=256),
    window_seconds: int | None = Query(default=None, ge=60, le=30 * 86400),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_user),
) -> RunListResponse:
    pool: asyncpg.Pool = request.app.state.pg
    scope = await resolve_project_scope(pool, project_id, principal)
    ch = _scoped_ch(request, scope)

    sql = """
        select run_id, name, kind, status, start_time, duration_ns,
               total_tokens, cost_usd, sdk
        from run final
        where project_id = {project_id:UUID}
    """
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if status_filter is not None:
        sql += " and status = {status_filter:String}"
        params["status_filter"] = status_filter
    if kind is not None:
        sql += " and kind = {kind:String}"
        params["kind"] = kind
    if search:
        # Case-insensitive substring on `name`, plus run_id prefix so a
        # pasted/remembered id fragment (the ids shown in every table)
        # resolves. LowCardinality(String) is fine with
        # positionCaseInsensitive and this isn't the hot path -- the
        # index is by start_time + project, search is a UI convenience.
        sql += (
            " and (positionCaseInsensitive(name, {search:String}) > 0"
            "      or startsWith(toString(run_id), lower({search:String})))"
        )
        params["search"] = search
    if window_seconds is not None:
        sql += " and start_time >= now64(9) - toIntervalSecond({window:UInt32})"
        params["window"] = window_seconds
    sql += " order by start_time desc limit {limit:UInt32} offset {offset:UInt32}"

    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("clickhouse query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items = [
        RunListItem(
            run_id=row["run_id"],
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            start_time=row["start_time"],
            latency_ms=_latency_ms(row.get("duration_ns")),
            total_tokens=int(row["total_tokens"] or 0),
            cost_usd=float(row["cost_usd"] or 0),
            sdk=row["sdk"],
        )
        for row in rows
    ]
    return RunListResponse(items=items)


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    request: Request,
    run_id: UUID,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> RunDetail:
    pool: asyncpg.Pool = request.app.state.pg
    scope = await resolve_project_scope(pool, project_id, principal)
    ch = _scoped_ch(request, scope)

    sql = """
        select run_id, project_id, parent_run_id, name, kind, status,
               start_time, end_time, duration_ns,
               inputs, outputs,
               prompt_tokens, completion_tokens, total_tokens, cost_usd,
               sdk, sdk_version, session_id, user_id, tags, metadata,
               error_kind, error_message
        from run final
        where project_id = {project_id:UUID}
          and run_id = {run_id:UUID}
        limit 1
    """
    params = {"run_id": str(run_id)}
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("run detail query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    row = rows[0]
    return RunDetail(
        run_id=row["run_id"],
        project_id=row["project_id"],
        parent_run_id=row.get("parent_run_id"),
        name=row["name"],
        kind=row["kind"],
        status=row["status"],
        start_time=row["start_time"],
        end_time=row.get("end_time"),
        latency_ms=_latency_ms(row.get("duration_ns")),
        inputs=row.get("inputs") or "",
        outputs=row.get("outputs") or "",
        prompt_tokens=int(row.get("prompt_tokens") or 0),
        completion_tokens=int(row.get("completion_tokens") or 0),
        total_tokens=int(row.get("total_tokens") or 0),
        cost_usd=float(row.get("cost_usd") or 0),
        sdk=row.get("sdk") or "",
        sdk_version=row.get("sdk_version") or "",
        session_id=row.get("session_id"),
        user_id=row.get("user_id"),
        tags=list(row.get("tags") or []),
        metadata=row.get("metadata") or "",
        error_kind=row.get("error_kind") or "",
        error_message=row.get("error_message") or "",
    )


@router.get("/{run_id}/spans", response_model=SpanListResponse)
async def list_spans(
    request: Request,
    run_id: UUID,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> SpanListResponse:
    pool: asyncpg.Pool = request.app.state.pg
    scope = await resolve_project_scope(pool, project_id, principal)
    ch = _scoped_ch(request, scope)

    sql = """
        select span_id, parent_span_id, name, kind, status,
               start_time, end_time, duration_ns,
               inputs, outputs, model, temperature,
               prompt_tokens, completion_tokens, total_tokens, cost_usd,
               error_kind, error_message, attributes
        from span final
        where project_id = {project_id:UUID}
          and run_id = {run_id:UUID}
        order by start_time asc
        limit 5000
    """
    params = {"run_id": str(run_id)}
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("span list query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items = [
        SpanItem(
            span_id=row["span_id"],
            parent_span_id=row.get("parent_span_id"),
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            start_time=row["start_time"],
            end_time=row.get("end_time"),
            latency_ms=_latency_ms(row.get("duration_ns")),
            inputs=row.get("inputs") or "",
            outputs=row.get("outputs") or "",
            model=row.get("model") or "",
            temperature=(float(row["temperature"]) if row.get("temperature") is not None else None),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_usd=float(row.get("cost_usd") or 0),
            error_kind=row.get("error_kind") or "",
            error_message=row.get("error_message") or "",
            attributes=row.get("attributes") or "",
        )
        for row in rows
    ]
    return SpanListResponse(items=items)


def _scoped_ch(request: Request, scope) -> ScopedClickHouse:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ScopedClickHouse(ch, scope)


def _latency_ms(duration_ns: object) -> float | None:
    if duration_ns is None:
        return None
    try:
        return round(int(duration_ns) / 1_000_000, 3)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
