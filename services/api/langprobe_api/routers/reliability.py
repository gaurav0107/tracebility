"""Eval-reliability read endpoint.

GET /v1/eval-reliability?project_id=&eval_config_id= — schema-adherence,
test-retest stability, and inter-judge agreement for an eval config, computed
over the existing eval_score store. Authed (viewer+), project-scoped.
"""

from __future__ import annotations

from dataclasses import asdict

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..reliability.metrics import compute_reliability

log = structlog.get_logger("langprobe.api.reliability")

router = APIRouter(prefix="/v1", tags=["eval-reliability"])


@router.get("/eval-reliability")
async def eval_reliability(
    request: Request,
    project_id: str = Query(...),
    eval_config_id: str = Query(...),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    limit: int = Query(default=10000, ge=1, le=200000),
    principal: Principal = Depends(require_user),
):
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

    try:
        rows = await ch.query(
            """
            select toString(coalesce(span_id, run_id)) as item_key,
                   judge_name, score, outcome
              from eval_score final
             where project_id = {project_id:UUID}
               and eval_config_id = {eval_config_id:UUID}
             limit {limit:UInt32}
            """,
            parameters={
                "project_id": project_id,
                "eval_config_id": eval_config_id,
                "limit": limit,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("eval-reliability query failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable"
        ) from exc

    report = compute_reliability(
        [
            {
                "item_key": str(r.get("item_key") or ""),
                "judge_name": str(r.get("judge_name") or ""),
                "score": float(r.get("score") or 0.0),
                "outcome": str(r.get("outcome") or "ok"),
            }
            for r in rows
        ],
        threshold=threshold,
    )
    return asdict(report)
