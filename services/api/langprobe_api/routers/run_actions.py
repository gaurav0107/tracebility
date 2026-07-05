"""Bulk actions on a selection of runs.

Operators select N runs in /runs (checkbox column) and want to:
  - add them to a dataset (one dataset_item per run, source_run_id set
    so the dataset row points back to the trace)
  - send them to an existing annotation queue for human review

Both flows pull the run row from ClickHouse to derive input/output
text, then write through the same paths as the per-row routers (dataset
items live in ClickHouse `dataset_item`, annotation items live in
postgres `annotation_item`). The selection size is capped at 200; past
that, offer a saved view + "queue everything matching this filter" as
a future iteration.

ER-23: never silent-drop. If a run can't be resolved or fails to
materialize on the destination, it's reported back to the UI with a
per-run error so the operator can retry the rest. Successes still
land in their respective stores.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids

log = structlog.get_logger("langprobe.api.run_actions")

router = APIRouter(prefix="/v1/runs/_actions", tags=["run-actions"])

_MAX_SELECTION = 200


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class AddToDatasetBody(BaseModel):
    project_id: UUID
    dataset_id: UUID
    run_ids: list[UUID] = Field(min_length=1, max_length=_MAX_SELECTION)


class AddToAnnotationQueueBody(BaseModel):
    project_id: UUID
    queue_id: UUID
    run_ids: list[UUID] = Field(min_length=1, max_length=_MAX_SELECTION)


class BulkResultRow(BaseModel):
    run_id: UUID
    ok: bool
    error: str | None = None


class BulkResult(BaseModel):
    accepted: int
    skipped: int
    rows: list[BulkResultRow]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/add-to-dataset", response_model=BulkResult)
async def add_to_dataset(
    request: Request,
    body: AddToDatasetBody,
    principal: Principal = Depends(require_user),
) -> BulkResult:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )
    org_id, workspace_id = await resolve_tenant_ids(pool, body.project_id)
    dataset = await pool.fetchrow(
        """select id, project_id, item_count from dataset
             where id = $1 and deleted_at is null""",
        body.dataset_id,
    )
    if dataset is None or dataset["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")

    ch = _require_clickhouse(request)
    runs_by_id, missing = await _resolve_runs(ch, body.project_id, body.run_ids)

    rows: list[tuple[Any, ...]] = []
    accepted_ids: list[UUID] = []
    result_rows: list[BulkResultRow] = []
    now = datetime.utcnow()

    for run_id in body.run_ids:
        row = runs_by_id.get(run_id)
        if row is None:
            result_rows.append(BulkResultRow(run_id=run_id, ok=False, error="run not found"))
            continue
        item_id = uuid4()
        rows.append(
            (
                str(org_id),
                str(workspace_id),
                str(body.project_id),
                str(body.dataset_id),
                str(item_id),
                row.get("inputs") or "",
                row.get("outputs") or "",
                _json.dumps({"bulk_imported": True}),
                str(run_id),
                None,
                now,
                None,
            )
        )
        accepted_ids.append(run_id)
        result_rows.append(BulkResultRow(run_id=run_id, ok=True))

    if rows:
        try:
            await ch.insert(
                "dataset_item",
                rows,
                column_names=[
                    "org_id",
                    "workspace_id",
                    "project_id",
                    "dataset_id",
                    "item_id",
                    "input",
                    "expected",
                    "metadata",
                    "source_run_id",
                    "source_span_id",
                    "created_at",
                    "deleted_at",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("bulk dataset_item insert failed", error=str(exc))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "data plane unavailable",
            ) from exc

        await pool.execute(
            "update dataset set item_count = item_count + $2, updated_at = now() where id = $1",
            body.dataset_id,
            len(accepted_ids),
        )

    await audit.record(
        pool,
        principal=principal,
        action="run_actions.add_to_dataset",
        target_kind="dataset",
        target_id=body.dataset_id,
        payload={
            "accepted": len(accepted_ids),
            "skipped": len(missing),
            "run_count": len(body.run_ids),
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return BulkResult(
        accepted=len(accepted_ids),
        skipped=len(missing),
        rows=result_rows,
    )


@router.post("/add-to-annotation-queue", response_model=BulkResult)
async def add_to_annotation_queue(
    request: Request,
    body: AddToAnnotationQueueBody,
    principal: Principal = Depends(require_user),
) -> BulkResult:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )
    queue = await pool.fetchrow(
        """select id, project_id, item_total from annotation_queue
             where id = $1""",
        body.queue_id,
    )
    if queue is None or queue["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "annotation queue not found")

    ch = _require_clickhouse(request)
    runs_by_id, missing = await _resolve_runs(ch, body.project_id, body.run_ids)

    result_rows: list[BulkResultRow] = []
    accepted_ids: list[UUID] = []
    pairs: list[tuple[UUID, UUID, UUID]] = []  # (queue_id, project_id, run_id)
    for run_id in body.run_ids:
        if run_id not in runs_by_id:
            result_rows.append(BulkResultRow(run_id=run_id, ok=False, error="run not found"))
            continue
        accepted_ids.append(run_id)
        pairs.append((body.queue_id, body.project_id, run_id))

    if pairs:
        async with pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                """
                    with ins as (
                        insert into annotation_item (
                            queue_id, project_id, run_id, status
                        )
                        select q.queue_id, q.project_id, q.run_id, 'pending'
                          from unnest($1::uuid[], $2::uuid[], $3::uuid[])
                            as q(queue_id, project_id, run_id)
                        on conflict (queue_id, run_id) do nothing
                        returning 1
                    )
                    select count(*) from ins
                    """,
                [p[0] for p in pairs],
                [p[1] for p in pairs],
                [p[2] for p in pairs],
            )
            inserted_int = int(inserted or 0)
            if inserted_int:
                await conn.execute(
                    """update annotation_queue
                              set item_total = item_total + $2,
                                  status = case
                                      when status = 'complete'
                                        then 'open'
                                      else status
                                  end
                            where id = $1""",
                    body.queue_id,
                    inserted_int,
                )
        # Mark dedup-skips: the run was accepted in input but already in queue.
        # We can't easily know which specific ones were dedup'd here, so we
        # leave per-row results as ok=true and let the UI count via the
        # diff between accepted and skipped totals.
        for run_id in accepted_ids:
            # all are reported ok; dedup counts in the BulkResult totals
            result_rows.append(BulkResultRow(run_id=run_id, ok=True))
    else:
        inserted_int = 0

    await audit.record(
        pool,
        principal=principal,
        action="run_actions.add_to_annotation_queue",
        target_kind="annotation_queue",
        target_id=body.queue_id,
        payload={
            "accepted": inserted_int,
            "skipped": len(missing) + (len(accepted_ids) - inserted_int),
            "run_count": len(body.run_ids),
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return BulkResult(
        accepted=inserted_int,
        skipped=len(missing) + (len(accepted_ids) - inserted_int),
        rows=result_rows,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_runs(
    ch: ClickHouseQuery,
    project_id: UUID,
    run_ids: list[UUID],
) -> tuple[dict[UUID, dict[str, Any]], list[UUID]]:
    """Fetch the named runs from ClickHouse, return a dict + missing list."""
    if not run_ids:
        return {}, []
    placeholders = ", ".join(f"{{run_{i}:UUID}}" for i in range(len(run_ids)))
    sql = f"""
        select run_id, inputs, outputs
          from run final
         where project_id = {{project_id:UUID}}
           and run_id in ({placeholders})
    """
    params: dict[str, object] = {"project_id": str(project_id)}
    for idx, rid in enumerate(run_ids):
        params[f"run_{idx}"] = str(rid)
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("bulk run resolve failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "data plane unavailable",
        ) from exc

    found: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("run_id")
        if isinstance(rid, UUID):
            found[rid] = row
        else:
            try:
                found[UUID(str(rid))] = row
            except (TypeError, ValueError):
                continue
    missing = [r for r in run_ids if r not in found]
    return found, missing


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


def _require_clickhouse(request: Request) -> ClickHouseQuery:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ch
