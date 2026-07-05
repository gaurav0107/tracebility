"""Replay capture index — read-side surface.

The ingest worker derives a ``replay_capture`` row for every span of
kind tool/llm/retriever; this router exposes that index per run so the
UI can show "this run is replayable, here are the boundary I/Os we
captured" and a future replay-runner can fetch the artifact references.

The actual capture artifacts (tool I/O, LLM responses, retrieval docs)
are content-addressed by sha256. Today the bytes live inline on the
span row itself and ``object_ref`` reads ``inline:sha256:<hash>``;
when the object-store backend lands, the worker will flip the ref to
``s3://...`` without changing this read path.

Determinism note (ER-18): a model endpoint version diff is warned, not
silent-substituted. The replay-runner that consumes this index is the
right place to enforce that. Here we just surface the captures.
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

log = structlog.get_logger("langprobe.api.replays")

router = APIRouter(tags=["replays"])
runs_router = APIRouter(prefix="/v1/runs", tags=["replays"])
catalog_router = APIRouter(prefix="/v1/replays", tags=["replays"])

# `runs_router` carries the per-run replay-capture index (mounted on
# /v1/runs/{run_id}/replay-captures). `catalog_router` carries the
# cross-run replay catalog (mounted on /v1/replays/runs) — distinct
# URL space so it doesn't collide with the runs_query parameterized
# `/{run_id}` route. The module-level `router` is preserved so the
# existing app.include_router(replays.router) include still works;
# we re-export both sub-routers below.


class ReplayCaptureItem(BaseModel):
    span_id: UUID
    kind: str
    content_hash: str
    object_ref: str
    size_bytes: int
    attributes: str
    captured_at: datetime


class ReplayCaptureSummary(BaseModel):
    total: int
    by_kind: dict[str, int]
    bytes_total: int
    unique_hashes: int


class ReplayCaptureList(BaseModel):
    summary: ReplayCaptureSummary
    items: list[ReplayCaptureItem]


class ReplayableRun(BaseModel):
    run_id: UUID
    name: str
    kind: str
    status: str
    start_time: datetime
    capture_count: int
    bytes_total: int
    unique_hashes: int
    by_kind: dict[str, int]


class ReplayableRunList(BaseModel):
    items: list[ReplayableRun]


@catalog_router.get("/runs", response_model=ReplayableRunList)
async def list_replayable_runs(
    request: Request,
    project_id: UUID = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_user),
) -> ReplayableRunList:
    """List the most recent runs that have replay captures.

    Cross-run scan: GROUP BY run_id on replay_capture, then JOIN
    against run for name/kind/status/start_time. We bound to recent
    runs to keep the scan cheap; pagination + arbitrary windows can
    come when an operator hits this in anger.
    """
    pool: asyncpg.Pool = request.app.state.pg
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

    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )

    sql = """
        with caps as (
            select run_id,
                   count() as capture_count,
                   sum(size_bytes) as bytes_total,
                   uniqExact(content_hash) as unique_hashes,
                   groupArray((kind, 1)) as by_kind_pairs,
                   max(captured_at) as last_capture_at
              from replay_capture final
             where project_id = {project_id:UUID}
             group by run_id
        )
        select c.run_id as run_id,
               r.name as name,
               r.kind as kind,
               r.status as status,
               r.start_time as start_time,
               c.capture_count as capture_count,
               c.bytes_total as bytes_total,
               c.unique_hashes as unique_hashes,
               c.by_kind_pairs as by_kind_pairs
          from caps c
          left join run as r final
                 on r.project_id = {project_id:UUID}
                and r.run_id = c.run_id
         order by c.last_capture_at desc
         limit {limit:UInt32}
    """
    params = {"project_id": str(project_id), "limit": limit}
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("replayable runs query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items: list[ReplayableRun] = []
    for row in rows:
        # by_kind_pairs is Array(Tuple(String, UInt8)); accumulate.
        by_kind: dict[str, int] = {}
        for entry in row.get("by_kind_pairs") or []:
            try:
                kind = str(entry[0] or "")
            except (TypeError, IndexError):
                continue
            if kind:
                by_kind[kind] = by_kind.get(kind, 0) + 1
        items.append(
            ReplayableRun(
                run_id=row["run_id"],
                name=str(row.get("name") or ""),
                kind=str(row.get("kind") or ""),
                status=str(row.get("status") or ""),
                start_time=row["start_time"],
                capture_count=int(row.get("capture_count") or 0),
                bytes_total=int(row.get("bytes_total") or 0),
                unique_hashes=int(row.get("unique_hashes") or 0),
                by_kind=by_kind,
            )
        )
    return ReplayableRunList(items=items)


@runs_router.get("/{run_id}/replay-captures", response_model=ReplayCaptureList)
async def list_replay_captures(
    request: Request,
    run_id: UUID,
    project_id: UUID = Query(...),
    limit: int = Query(default=500, ge=1, le=5000),
    principal: Principal = Depends(require_user),
) -> ReplayCaptureList:
    pool: asyncpg.Pool = request.app.state.pg
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

    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )

    sql = """
        select span_id, kind, content_hash, object_ref, size_bytes,
               attributes, captured_at
          from replay_capture final
         where project_id = {project_id:UUID}
           and run_id = {run_id:UUID}
         order by captured_at asc
         limit {limit:UInt32}
    """
    params = {
        "project_id": str(project_id),
        "run_id": str(run_id),
        "limit": limit,
    }
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("replay_capture query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items: list[ReplayCaptureItem] = []
    by_kind: dict[str, int] = {}
    bytes_total = 0
    hashes: set[str] = set()
    for row in rows:
        kind = str(row.get("kind") or "")
        size = int(row.get("size_bytes") or 0)
        content_hash = str(row.get("content_hash") or "")
        items.append(
            ReplayCaptureItem(
                span_id=row["span_id"],
                kind=kind,
                content_hash=content_hash,
                object_ref=str(row.get("object_ref") or ""),
                size_bytes=size,
                attributes=str(row.get("attributes") or ""),
                captured_at=row["captured_at"],
            )
        )
        by_kind[kind] = by_kind.get(kind, 0) + 1
        bytes_total += size
        if content_hash:
            hashes.add(content_hash)

    return ReplayCaptureList(
        summary=ReplayCaptureSummary(
            total=len(items),
            by_kind=by_kind,
            bytes_total=bytes_total,
            unique_hashes=len(hashes),
        ),
        items=items,
    )
