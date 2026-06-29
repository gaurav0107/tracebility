"""Agent-view HTTP endpoints — the agent-first surface over REST.

Token-budgeted, LLM-legible views any agent can call with a session/API key:
- GET /v1/agent/failed-runs  — recent errored runs (entry point)
- GET /v1/runs/{run_id}/agent-view — the salient slice of one run

Same handlers the MCP server wraps (`agent.tools`); same auth as the rest of the
API. Reads require viewer+; the data plane (ClickHouse) is project-scoped.
"""

from __future__ import annotations

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..agent.tools import find_failed_runs, get_run_agent_view
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery

log = structlog.get_logger("langprobe.api.agent_views")

router = APIRouter(prefix="/v1", tags=["agent"])


async def _authorize(request: Request, principal: Principal, project_id):
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
    return ch


@router.get("/agent/failed-runs")
async def agent_failed_runs(
    request: Request,
    project_id: str = Query(...),
    limit: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require_user),
):
    ch = await _authorize(request, principal, project_id)
    try:
        runs = await find_failed_runs(ch, project_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("agent failed-runs query failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable"
        ) from exc
    return {"runs": runs}


@router.get("/runs/{run_id}/agent-view")
async def agent_run_view(
    request: Request,
    run_id: str,
    project_id: str = Query(...),
    token_budget: int = Query(default=2000, ge=200, le=20000),
    principal: Principal = Depends(require_user),
):
    ch = await _authorize(request, principal, project_id)
    try:
        view = await get_run_agent_view(
            ch, project_id, run_id, token_budget=token_budget
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("agent run-view query failed", run_id=run_id, error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable"
        ) from exc
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return view
