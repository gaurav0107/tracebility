"""Replay execution endpoint — Phase 0 span-level what-if.

``POST /v1/runs/{run_id}/replay`` re-dispatches the edited span(s) live, holds
the rest at captured values, computes the diff, and persists a ``replay_run``
row (schema 0003). The response carries the diff + an agent-legible summary
(the same `summarize_diff` the MCP `replay_run` tool returns).

Thin by design: auth/RBAC here, all replay logic in ``replay.service`` (shared
with the MCP tool) and ``replay.*`` (unit-tested).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..replay.executor import ReplayEdit
from ..replay.record import summarize_diff
from ..replay.service import run_span_replay

log = structlog.get_logger("langprobe.api.replay_runs")

runs_router = APIRouter(prefix="/v1/runs", tags=["replays"])


class ReplayEditIn(BaseModel):
    target_span_id: str = Field(min_length=1, max_length=128)
    field: str = Field(pattern="^(prompt|model|temperature|tool_args)$")
    value: Any = None


class ReplayRequest(BaseModel):
    project_id: UUID
    edits: list[ReplayEditIn] = Field(default_factory=list, max_length=64)


class SpanDeltaOut(BaseModel):
    span_id: str
    name: str
    diverged: bool
    output_changed: bool
    model_changed: bool
    cost_delta_usd: float
    latency_delta_ms: int
    note: str


class ReplayResponse(BaseModel):
    replay_run_id: str
    original_run_id: str
    determinism: str
    outcome: str
    span_count_total: int
    span_count_diverged: int
    summary: str
    deltas: list[SpanDeltaOut]


@runs_router.post("/{run_id}/replay", response_model=ReplayResponse)
async def replay_run(
    request: Request,
    run_id: UUID,
    body: ReplayRequest,
    principal: Principal = Depends(require_user),
) -> ReplayResponse:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await pool.fetchval(
        "select workspace_id from project where id = $1 and deleted_at is null",
        body.project_id,
    )
    if workspace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin", "member"),
    )

    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )

    edits = [
        ReplayEdit(target_span_id=e.target_span_id, field=e.field, value=e.value)
        for e in body.edits
    ]
    replay_run_id = uuid4()
    started_at = datetime.now(UTC)

    try:
        diff = await run_span_replay(
            pool,
            ch,
            project_id=body.project_id,
            run_id=run_id,
            edits=edits,
            replay_run_id=replay_run_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("replay failed", run_id=str(run_id), error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable"
        ) from exc

    if diff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run has no spans")

    return ReplayResponse(
        replay_run_id=str(replay_run_id),
        original_run_id=str(run_id),
        determinism=diff.determinism,
        outcome=diff.outcome,
        span_count_total=diff.span_count_total,
        span_count_diverged=diff.span_count_diverged,
        summary=summarize_diff(diff),
        deltas=[
            SpanDeltaOut(
                span_id=d.span_id,
                name=d.name,
                diverged=d.diverged,
                output_changed=d.output_changed,
                model_changed=d.model_changed,
                cost_delta_usd=d.cost_delta_usd,
                latency_delta_ms=d.latency_delta_ms,
                note=d.note,
            )
            for d in diff.deltas
        ],
    )
