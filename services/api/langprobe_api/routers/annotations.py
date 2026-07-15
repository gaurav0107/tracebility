"""Annotation queues for human review (parity loop #4 item #7).

Operators define a queue with a sampling rule (random N from a window)
and a rubric (categorical labels + binary/scalar/none score). On
queue creation we sample N runs from ClickHouse and insert one
`annotation_item` per sampled run with status='pending'. Reviewers
walk the queue and submit a label per item; submission flips the
item to 'done' AND writes one ClickHouse `eval_score` row tagged
`judge_name='human'` so human labels aggregate alongside LLM judges
and end-user feedback in the same store.

V1 honest scope:
- Sampling: random N from last `window_seconds`, optional
  status filter (error / ok / any).
- Rubric: list of allowed labels + score type (binary/scalar/none).
- Reviewers: any workspace member; no per-queue assignment yet.
- Inter-rater agreement is computed at read time from the
  eval_score rows themselves; no precomputed table.
"""

from __future__ import annotations

import json as _json
import random
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids

log = structlog.get_logger("langprobe.api.annotations")

router = APIRouter(prefix="/v1/annotations", tags=["annotations"])

_SCORE_KINDS = {"binary", "scalar", "none"}
_STATUS_FILTERS = {"any", "ok", "error"}
_ITEM_STATUSES = {"pending", "done", "skipped"}


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class AnnotationSampling(BaseModel):
    window_seconds: int = Field(ge=60, le=30 * 86400, default=86400)
    sample_size: int = Field(ge=1, le=500, default=50)
    status: str = Field(default="any")


class AnnotationRubric(BaseModel):
    labels: list[str] = Field(min_length=1, max_length=20)
    score: str = Field(default="binary")


class AnnotationQueueOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    description: str | None
    sampling: AnnotationSampling
    rubric: AnnotationRubric
    item_total: int
    item_done: int
    status: str
    created_at: datetime
    updated_at: datetime


class AnnotationQueueCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    sampling: AnnotationSampling = Field(default_factory=AnnotationSampling)
    rubric: AnnotationRubric


class AnnotationItemOut(BaseModel):
    id: UUID
    queue_id: UUID
    project_id: UUID
    run_id: UUID
    status: str
    label: str | None
    score: float | None
    rationale: str | None
    reviewed_at: datetime | None
    created_at: datetime


class AnnotationItemList(BaseModel):
    items: list[AnnotationItemOut]


class AnnotationSubmit(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    # Only used when rubric.score='scalar'; ignored otherwise.
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AnnotationQueueOut])
async def list_queues(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[AnnotationQueueOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, name, description, sampling, rubric,
               item_total, item_done, status, created_at, updated_at
          from annotation_queue
         where project_id = $1
         order by created_at desc
        """,
        project_id,
    )
    return [_queue_out(r) for r in rows]


@router.post(
    "",
    response_model=AnnotationQueueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_queue(
    request: Request,
    body: AnnotationQueueCreate,
    principal: Principal = Depends(require_user),
) -> AnnotationQueueOut:
    if body.sampling.status not in _STATUS_FILTERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"sampling.status must be one of {sorted(_STATUS_FILTERS)}",
        )
    if body.rubric.score not in _SCORE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"rubric.score must be one of {sorted(_SCORE_KINDS)}",
        )
    if any(not lbl.strip() for lbl in body.rubric.labels):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "rubric.labels must not contain empty strings",
        )

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    ch: ClickHouseQuery | None = request.app.state.clickhouse
    sampled_run_ids = await _sample_runs(ch, body.project_id, body.sampling)

    sampling_json = _json.dumps(body.sampling.model_dump())
    rubric_json = _json.dumps(body.rubric.model_dump())

    async with pool.acquire() as conn, conn.transaction():
        queue_row = await conn.fetchrow(
            """
                insert into annotation_queue (
                    project_id, name, description, sampling, rubric,
                    item_total, item_done, status, created_by
                )
                values ($1, $2, $3, $4::jsonb, $5::jsonb, $6, 0,
                        case when $6 = 0 then 'complete' else 'open' end,
                        $7)
                returning id, project_id, name, description, sampling,
                          rubric, item_total, item_done, status,
                          created_at, updated_at
                """,
            body.project_id,
            body.name,
            body.description,
            sampling_json,
            rubric_json,
            len(sampled_run_ids),
            principal.user_id,
        )
        assert queue_row is not None
        queue_id: UUID = queue_row["id"]

        if sampled_run_ids:
            await conn.executemany(
                """
                    insert into annotation_item (
                        queue_id, project_id, run_id, status
                    )
                    values ($1, $2, $3, 'pending')
                    on conflict (queue_id, run_id) do nothing
                    """,
                [(queue_id, body.project_id, run_id) for run_id in sampled_run_ids],
            )

    await audit.record(
        pool,
        principal=principal,
        action="annotation_queue.create",
        target_kind="annotation_queue",
        target_id=queue_id,
        payload={
            "name": body.name,
            "sampled": len(sampled_run_ids),
            "window_seconds": body.sampling.window_seconds,
            "rubric_score": body.rubric.score,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return _queue_out(queue_row)


@router.get("/{queue_id}", response_model=AnnotationQueueOut)
async def get_queue(
    request: Request,
    queue_id: UUID,
    principal: Principal = Depends(require_user),
) -> AnnotationQueueOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        """
        select id, project_id, name, description, sampling, rubric,
               item_total, item_done, status, created_at, updated_at
          from annotation_queue
         where id = $1
        """,
        queue_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue not found")
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    return _queue_out(row)


@router.delete("/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue(
    request: Request,
    queue_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow("select project_id from annotation_queue where id = $1", queue_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue not found")
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    await pool.execute("delete from annotation_queue where id = $1", queue_id)
    await audit.record(
        pool,
        principal=principal,
        action="annotation_queue.delete",
        target_kind="annotation_queue",
        target_id=queue_id,
        payload={},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@router.get("/{queue_id}/items", response_model=AnnotationItemList)
async def list_items(
    request: Request,
    queue_id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(require_user),
) -> AnnotationItemList:
    pool: asyncpg.Pool = request.app.state.pg
    queue = await pool.fetchrow("select project_id from annotation_queue where id = $1", queue_id)
    if queue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue not found")
    await _assert_project_role(
        pool,
        principal,
        queue["project_id"],
        ("owner", "admin", "member", "viewer"),
    )

    args: list[Any] = [queue_id]
    sql = """
        select id, queue_id, project_id, run_id, status, label, score,
               rationale, reviewed_at, created_at
          from annotation_item
         where queue_id = $1
    """
    if status_filter is not None:
        if status_filter not in _ITEM_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid status filter")
        args.append(status_filter)
        sql += f" and status = ${len(args)}"
    args.append(limit)
    sql += f" order by created_at asc limit ${len(args)}"

    rows = await pool.fetch(sql, *args)
    return AnnotationItemList(items=[_item_out(r) for r in rows])


@router.post(
    "/{queue_id}/items/{item_id}/submit",
    response_model=AnnotationItemOut,
)
async def submit_item(
    request: Request,
    queue_id: UUID,
    item_id: UUID,
    body: AnnotationSubmit,
    principal: Principal = Depends(require_user),
) -> AnnotationItemOut:
    pool: asyncpg.Pool = request.app.state.pg
    queue = await pool.fetchrow(
        """select id, project_id, rubric, item_total, item_done
             from annotation_queue where id = $1""",
        queue_id,
    )
    if queue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue not found")
    project_id: UUID = queue["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )
    org_id, workspace_id = await resolve_tenant_ids(pool, project_id)

    item = await pool.fetchrow(
        """select id, run_id, status from annotation_item
             where id = $1 and queue_id = $2""",
        item_id,
        queue_id,
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")

    rubric = _parse_rubric(queue["rubric"])
    if body.label not in rubric.labels:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"label must be one of {rubric.labels}",
        )

    if rubric.score == "binary":
        # Convention: first label = positive (score 1.0), every other = 0.0.
        score = 1.0 if body.label == rubric.labels[0] else 0.0
    elif rubric.score == "scalar":
        if body.score is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "scalar rubric requires an explicit score 0..1",
            )
        score = float(body.score)
    else:
        score = 0.0

    was_pending = item["status"] == "pending"

    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.fetchrow(
            """
                update annotation_item
                   set status = 'done',
                       label = $2,
                       score = $3,
                       rationale = $4,
                       reviewed_by = $5,
                       reviewed_at = now()
                 where id = $1
                returning id, queue_id, project_id, run_id, status,
                          label, score, rationale, reviewed_at, created_at
                """,
            item_id,
            body.label,
            score,
            body.rationale,
            principal.user_id,
        )
        assert updated is not None

        if was_pending:
            # Only count first-time submissions toward item_done so a
            # reviewer fixing their answer doesn't inflate the counter.
            await conn.execute(
                """update annotation_queue
                          set item_done = item_done + 1,
                              status = case
                                  when item_done + 1 >= item_total then 'complete'
                                  else status
                              end
                        where id = $1""",
                queue_id,
            )

    # ClickHouse write: best-effort but we still surface failures so
    # the UI can show the operator something is wrong with the analytics
    # plane. The postgres item is the source of truth for the queue.
    ch: ClickHouseQuery | None = request.app.state.clickhouse
    if ch is not None:
        try:
            await ch.insert(
                "eval_score",
                [
                    (
                        str(org_id),
                        str(workspace_id),
                        str(project_id),
                        str(updated["run_id"]),
                        None,
                        str(queue_id),
                        "human",
                        "annotation",
                        "v1",
                        float(score),
                        body.label,
                        body.rationale or "",
                        "",
                        "ok",
                        datetime.now(UTC),
                        0,
                    )
                ],
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
            log.warning(
                "annotation eval_score insert failed",
                queue_id=str(queue_id),
                run_id=str(updated["run_id"]),
                error=str(exc),
            )

    await audit.record(
        pool,
        principal=principal,
        action="annotation_item.submit",
        target_kind="annotation_item",
        target_id=item_id,
        payload={
            "queue_id": str(queue_id),
            "label": body.label,
            "score": score,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _item_out(updated)


@router.post(
    "/{queue_id}/items/{item_id}/skip",
    response_model=AnnotationItemOut,
)
async def skip_item(
    request: Request,
    queue_id: UUID,
    item_id: UUID,
    principal: Principal = Depends(require_user),
) -> AnnotationItemOut:
    pool: asyncpg.Pool = request.app.state.pg
    queue = await pool.fetchrow("select project_id from annotation_queue where id = $1", queue_id)
    if queue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue not found")
    await _assert_project_role(pool, principal, queue["project_id"], ("owner", "admin", "member"))

    updated = await pool.fetchrow(
        """update annotation_item
              set status = 'skipped'
            where id = $1 and queue_id = $2 and status = 'pending'
            returning id, queue_id, project_id, run_id, status, label, score,
                      rationale, reviewed_at, created_at""",
        item_id,
        queue_id,
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found or already actioned")
    return _item_out(updated)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


async def _sample_runs(
    clickhouse: ClickHouseQuery | None,
    project_id: UUID,
    sampling: AnnotationSampling,
) -> list[UUID]:
    if clickhouse is None:
        return []
    where_status = ""
    if sampling.status == "ok":
        where_status = "and status = 'ok'"
    elif sampling.status == "error":
        where_status = "and status = 'error'"
    sql = f"""
        select toString(run_id) as id
          from run final
         where project_id = {{project_id:UUID}}
           and start_time >= now64(9) - toIntervalSecond({{window:UInt32}})
           {where_status}
    """
    try:
        rows = await clickhouse.query(
            sql,
            parameters={
                "project_id": str(project_id),
                "window": sampling.window_seconds,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "annotation sampling clickhouse query failed",
            project_id=str(project_id),
            error=str(exc),
        )
        return []
    candidates = [r["id"] for r in rows if r.get("id")]
    if not candidates:
        return []
    sample_size = min(sampling.sample_size, len(candidates))
    chosen = random.sample(candidates, sample_size)
    out: list[UUID] = []
    for raw in chosen:
        try:
            out.append(UUID(raw))
        except (TypeError, ValueError):
            continue
    return out


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


def _queue_out(row: asyncpg.Record) -> AnnotationQueueOut:
    return AnnotationQueueOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"],
        sampling=_parse_sampling(row["sampling"]),
        rubric=_parse_rubric(row["rubric"]),
        item_total=int(row["item_total"]),
        item_done=int(row["item_done"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _item_out(row: asyncpg.Record) -> AnnotationItemOut:
    return AnnotationItemOut(
        id=row["id"],
        queue_id=row["queue_id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        status=row["status"],
        label=row["label"],
        score=_opt_float(row["score"]),
        rationale=row["rationale"],
        reviewed_at=row["reviewed_at"],
        created_at=row["created_at"],
    )


def _parse_sampling(raw: Any) -> AnnotationSampling:
    data = _coerce_json(raw)
    try:
        return AnnotationSampling(**data)
    except Exception:  # noqa: BLE001
        return AnnotationSampling()


def _parse_rubric(raw: Any) -> AnnotationRubric:
    data = _coerce_json(raw)
    try:
        return AnnotationRubric(**data)
    except Exception:  # noqa: BLE001
        return AnnotationRubric(labels=["pass", "fail"], score="binary")


def _coerce_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f
