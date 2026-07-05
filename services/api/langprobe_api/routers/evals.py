"""Eval runs (single-judge v1).

An eval run scores every item in a dataset with a chosen judge and writes
one ClickHouse `eval_score` row per item. The catalog row in postgres
(`eval_run`) tracks lifecycle (queued → running → done/failed) and rolls
up `score_avg` so the list view stays cheap.

V1 ships built-in judges only — `echo` (always 1.0, smoke-test), `contains`
(1.0 if expected substring of input else 0.0), `exact` (1.0 if expected ==
input else 0.0). LLM-as-judge swaps in next iteration once API key config
lands; the data path (postgres lifecycle + ClickHouse rows) is identical.

Boundaries:
- POST inserts the queued row, kicks off `asyncio.create_task(_run_eval(...))`,
  and returns 202 with the row.
- The background task fetches dataset items from ClickHouse, scores each,
  inserts into ClickHouse `eval_score`, and updates postgres `eval_run`
  lifecycle counters.
- Audit-fail-closed (ER-10) on every write through `audit.record`.
- RBAC: list/get for any role; create for owner/admin/member; cancel/delete
  not supported in v1 (a stuck row is fine — no destructive ops).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids
from . import luna_judges

log = structlog.get_logger("langprobe.api.evals")

router = APIRouter(prefix="/v1/eval-runs", tags=["evals"])

_JUDGE_KINDS = {"echo", "contains", "exact"}


class EvalRunOut(BaseModel):
    id: UUID
    project_id: UUID
    dataset_id: UUID
    prompt_id: UUID | None
    prompt_version_id: UUID | None
    judge_kind: str
    name: str | None
    status: str
    item_total: int
    item_done: int
    score_avg: float | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvalRunCreate(BaseModel):
    project_id: UUID
    dataset_id: UUID
    judge_kind: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=255)
    prompt_id: UUID | None = None
    prompt_version_id: UUID | None = None


class EvalScoreOut(BaseModel):
    item_id: UUID | None
    score: float
    label: str
    rationale: str
    outcome: str
    judged_at: datetime


class EvalScoreList(BaseModel):
    scores: list[EvalScoreOut]


@router.get("", response_model=list[EvalRunOut])
async def list_runs(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[EvalRunOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        project_id,
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, dataset_id, prompt_id, prompt_version_id,
               judge_kind, name, status, item_total, item_done, score_avg,
               error, started_at, finished_at, created_at, updated_at
        from eval_run
        where project_id = $1
        order by created_at desc
        """,
        project_id,
    )
    return [EvalRunOut(**dict(r)) for r in rows]


@router.post("", response_model=EvalRunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    request: Request,
    body: EvalRunCreate,
    background: BackgroundTasks,
    principal: Principal = Depends(require_user),
) -> EvalRunOut:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool,
        body.project_id,
        principal,
        allowed=("owner", "admin", "member"),
    )
    # judge_kind accepts the built-in deterministic judges plus
    # `luna:<slug>` references to a user-authored prompted judge.
    # Validate luna refs at create-time so a stuck row isn't created
    # for a missing/deleted judge.
    base_kind, luna_slug = luna_judges.parse_judge_kind(body.judge_kind)
    if luna_slug is not None:
        judge_row = await luna_judges.resolve_judge(pool, body.project_id, luna_slug)
        if judge_row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"luna judge '{luna_slug}' not found in this project",
            )
    elif base_kind not in _JUDGE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"judge_kind must be one of {sorted(_JUDGE_KINDS)} or 'luna:<slug>'",
        )
    dataset = await pool.fetchrow(
        """
        select id, project_id, item_count
        from dataset
        where id = $1 and deleted_at is null
        """,
        body.dataset_id,
    )
    if dataset is None or dataset["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")

    if body.prompt_version_id and not body.prompt_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "prompt_version_id requires prompt_id",
        )

    row = await pool.fetchrow(
        """
        insert into eval_run (
            project_id, dataset_id, prompt_id, prompt_version_id,
            judge_kind, name, status, item_total, created_by
        )
        values ($1, $2, $3, $4, $5, $6, 'queued', $7, $8)
        returning id, project_id, dataset_id, prompt_id, prompt_version_id,
                  judge_kind, name, status, item_total, item_done, score_avg,
                  error, started_at, finished_at, created_at, updated_at
        """,
        body.project_id,
        body.dataset_id,
        body.prompt_id,
        body.prompt_version_id,
        body.judge_kind,
        body.name,
        dataset["item_count"],
        principal.user_id,
    )
    assert row is not None
    eval_run = EvalRunOut(**dict(row))
    await audit.record(
        pool,
        principal=principal,
        action="eval_run.create",
        target_kind="eval_run",
        target_id=eval_run.id,
        payload={
            "dataset_id": str(eval_run.dataset_id),
            "judge_kind": eval_run.judge_kind,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=eval_run.project_id,
    )

    ch = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        await _mark_failed(pool, eval_run.id, "clickhouse not configured")
    else:
        background.add_task(_run_eval, pool, ch, eval_run.id)
    return eval_run


@router.get("/{run_id}", response_model=EvalRunOut)
async def get_run(
    request: Request,
    run_id: UUID,
    principal: Principal = Depends(require_user),
) -> EvalRunOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_run(pool, run_id)
    await _assert_project_role(
        pool,
        row["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    return EvalRunOut(**dict(row))


@router.get("/{run_id}/scores", response_model=EvalScoreList)
async def list_scores(
    request: Request,
    run_id: UUID,
    limit: int = Query(default=200, ge=1, le=2000),
    principal: Principal = Depends(require_user),
) -> EvalScoreList:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_run(pool, run_id)
    await _assert_project_role(
        pool,
        row["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    ch = _require_clickhouse(request)

    sql = """
        select run_id, score, label, rationale, outcome, judged_at
        from eval_score
        where project_id = {project_id:UUID}
          and eval_config_id = {run_id:UUID}
        order by judged_at desc
        limit {limit:UInt32}
    """
    params = {
        "project_id": str(row["project_id"]),
        "run_id": str(run_id),
        "limit": limit,
    }
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("eval scores query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    scores = [
        EvalScoreOut(
            item_id=r["run_id"],
            score=float(r["score"]),
            label=str(r.get("label") or ""),
            rationale=str(r.get("rationale") or ""),
            outcome=str(r.get("outcome") or "ok"),
            judged_at=r["judged_at"],
        )
        for r in rows
    ]
    return EvalScoreList(scores=scores)


# ----- background runner ---------------------------------------------------


async def _run_eval(pool: asyncpg.Pool, ch: ClickHouseQuery, run_id: UUID) -> None:
    """Score every item in the dataset, write one ClickHouse row per item.

    Best-effort: failures bubble into `eval_run.error` with status='failed';
    we never silently drop. Per-item raw_output stays empty for built-in
    judges since there is no LLM call to record.
    """
    try:
        run = await pool.fetchrow(
            """
            select id, project_id, dataset_id, judge_kind
            from eval_run where id = $1
            """,
            run_id,
        )
        if run is None:
            return
        org_id, workspace_id = await resolve_tenant_ids(pool, run["project_id"])

        await pool.execute(
            "update eval_run set status='running', started_at=now() where id=$1",
            run_id,
        )

        items = await _fetch_dataset_items(ch, run["project_id"], run["dataset_id"])
        await pool.execute(
            "update eval_run set item_total=$2 where id=$1",
            run_id,
            len(items),
        )

        # Resolve luna judge once (if applicable) so per-item dispatch
        # doesn't re-hit postgres on every iteration.
        base_kind, luna_slug = luna_judges.parse_judge_kind(run["judge_kind"])
        luna_row: dict[str, Any] | None = None
        luna_api_key: str | None = None
        if luna_slug is not None:
            luna_row = await luna_judges.resolve_judge(pool, run["project_id"], luna_slug)
            if luna_row is None:
                await _mark_failed(
                    pool,
                    run_id,
                    f"luna judge '{luna_slug}' not found",
                )
                return
        rows: list[tuple[Any, ...]] = []
        score_sum = 0.0
        judged_at = datetime.utcnow()
        for item in items:
            if luna_row is not None:
                score, label, rationale, raw_output = await luna_judges.apply_luna_judge(
                    luna_row,
                    pool=pool,
                    project_id=run["project_id"],
                    surface="eval",
                    surface_ref_id=run_id,
                    input_text=item["input"],
                    expected=item["expected"],
                )
                judge_endpoint = luna_row["provider"]
                outcome = "ok" if label != "error" else "failed"
            else:
                score, label, rationale = _judge(base_kind, item["input"], item["expected"])
                raw_output = ""
                judge_endpoint = "builtin"
                outcome = "ok"
            score_sum += score
            rows.append(
                (
                    str(org_id),
                    str(workspace_id),
                    str(run["project_id"]),
                    str(item["item_id"]),  # carry item_id in run_id slot
                    None,  # span_id
                    str(run_id),  # eval_config_id = our run id
                    run["judge_kind"],  # judge_name (carries luna:slug)
                    judge_endpoint,  # judge_endpoint
                    "v1",  # judge_version
                    float(score),
                    label,
                    rationale,
                    raw_output,  # raw_output (LLM response, if any)
                    outcome,
                    judged_at,
                    0,  # cost_usd
                )
            )

        if rows:
            try:
                await ch.insert(
                    "eval_score",
                    rows,
                    column_names=[
                        "org_id",
                        "workspace_id",
                        "project_id",
                        "run_id",
                        "span_id",
                        "eval_config_id",
                        "judge_name",
                        "judge_endpoint",
                        "judge_version",
                        "score",
                        "label",
                        "rationale",
                        "raw_output",
                        "outcome",
                        "judged_at",
                        "cost_usd",
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                await _mark_failed(pool, run_id, f"clickhouse insert: {exc}")
                return

        avg = (score_sum / len(rows)) if rows else None
        await pool.execute(
            """
            update eval_run
            set status='done', item_done=$2, score_sum=$3, score_avg=$4,
                finished_at=now()
            where id=$1
            """,
            run_id,
            len(rows),
            score_sum,
            avg,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("eval run failed", run_id=str(run_id), error=str(exc))
        await _mark_failed(pool, run_id, str(exc))


async def _fetch_dataset_items(
    ch: ClickHouseQuery, project_id: UUID, dataset_id: UUID
) -> list[dict[str, Any]]:
    sql = """
        select item_id, input, expected
        from dataset_item final
        where project_id = {project_id:UUID}
          and dataset_id = {dataset_id:UUID}
          and deleted_at is null
        order by created_at asc
        limit 5000
    """
    params = {
        "project_id": str(project_id),
        "dataset_id": str(dataset_id),
    }
    return await ch.query(sql, parameters=params)


def _judge(kind: str, input_text: str, expected: str) -> tuple[float, str, str]:
    """Built-in deterministic judges. No LLM call, no API key."""
    if kind == "echo":
        return (1.0, "pass", "echo: smoke-test, always 1.0")
    if kind == "exact":
        ok = input_text == expected
        return (
            1.0 if ok else 0.0,
            "pass" if ok else "fail",
            "exact match" if ok else "input != expected",
        )
    if kind == "contains":
        ok = bool(expected) and (expected in input_text)
        return (
            1.0 if ok else 0.0,
            "pass" if ok else "fail",
            "expected found in input" if ok else "expected not in input",
        )
    return (0.0, "fail", f"unknown judge kind: {kind}")


async def _mark_failed(pool: asyncpg.Pool, run_id: UUID, reason: str) -> None:
    await pool.execute(
        """
        update eval_run
        set status='failed', error=$2, finished_at=now()
        where id=$1
        """,
        run_id,
        reason[:2000],
    )


# ----- helpers -------------------------------------------------------------


async def _fetch_run(pool: asyncpg.Pool, run_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, dataset_id, prompt_id, prompt_version_id,
               judge_kind, name, status, item_total, item_done, score_avg,
               error, started_at, finished_at, created_at, updated_at
        from eval_run
        where id = $1
        """,
        run_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "eval run not found")
    return row


async def _assert_project_role(
    pool: asyncpg.Pool,
    project_id: UUID,
    principal: Principal,
    *,
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


def _require_clickhouse(request: Request) -> ClickHouseQuery:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ch
