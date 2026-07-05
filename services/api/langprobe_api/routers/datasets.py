"""Datasets CRUD + items.

Datasets are collections of inputs (with optional reference outputs)
used for offline eval and regression suites. The catalog rows live in
postgres (`dataset` table); the rows themselves live in ClickHouse
(`dataset_item` table) because eval runs join them against `run` /
`span` and we want one storage system to scan.

Boundaries:
- list/create/patch/delete touch only postgres.
- list/create/delete *items* touch ClickHouse for the row, then bump
  `dataset.item_count` on postgres so the list view stays cheap.
- A delete is a soft delete on both sides (`deleted_at`) — never a real
  row delete, so audit trails remain intact (ER-23 spirit).
- Audit-fail-closed (ER-10): every write records an `audit_log` row in
  the same path; an audit failure surfaces as 500, not silent success.

This is the read+write surface; the eval runner that consumes it lands
in a later loop.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids

log = structlog.get_logger("langprobe.api.datasets")

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


class DatasetOut(BaseModel):
    id: UUID
    project_id: UUID
    slug: str
    name: str
    description: str | None
    item_count: int
    created_at: datetime
    updated_at: datetime


class DatasetCreate(BaseModel):
    project_id: UUID
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class DatasetItemOut(BaseModel):
    item_id: UUID
    dataset_id: UUID
    input: str
    expected: str
    metadata: dict[str, Any]
    source_run_id: UUID | None
    source_span_id: UUID | None
    created_at: datetime


class DatasetItemList(BaseModel):
    items: list[DatasetItemOut]


class DatasetItemCreate(BaseModel):
    input: str
    expected: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_run_id: UUID | None = None
    source_span_id: UUID | None = None


@router.get("", response_model=list[DatasetOut])
async def list_datasets(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[DatasetOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        project_id,
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, slug, name, description, item_count,
               created_at, updated_at
        from dataset
        where project_id = $1 and deleted_at is null
        order by created_at desc
        """,
        project_id,
    )
    return [DatasetOut(**dict(r)) for r in rows]


@router.post("", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    request: Request,
    body: DatasetCreate,
    principal: Principal = Depends(require_user),
) -> DatasetOut:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool,
        body.project_id,
        principal,
        allowed=("owner", "admin", "member"),
    )
    try:
        row = await pool.fetchrow(
            """
            insert into dataset (project_id, slug, name, description, created_by)
            values ($1, $2, $3, $4, $5)
            returning id, project_id, slug, name, description, item_count,
                      created_at, updated_at
            """,
            body.project_id,
            body.slug,
            body.name,
            body.description,
            principal.user_id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "dataset slug already exists in project"
        ) from exc
    assert row is not None
    dataset = DatasetOut(**dict(row))
    await audit.record(
        pool,
        principal=principal,
        action="dataset.create",
        target_kind="dataset",
        target_id=dataset.id,
        payload={"slug": dataset.slug, "name": dataset.name},
        request=request,
        workspace_id=workspace_id,
        project_id=dataset.project_id,
    )
    return dataset


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(
    request: Request,
    dataset_id: UUID,
    principal: Principal = Depends(require_user),
) -> DatasetOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_dataset_row(pool, dataset_id)
    await _assert_project_role(
        pool,
        row["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    return DatasetOut(**dict(row))


@router.patch("/{dataset_id}", response_model=DatasetOut)
async def update_dataset(
    request: Request,
    dataset_id: UUID,
    body: DatasetPatch,
    principal: Principal = Depends(require_user),
) -> DatasetOut:
    pool: asyncpg.Pool = request.app.state.pg
    existing = await _fetch_dataset_row(pool, dataset_id)
    workspace_id = await _assert_project_role(
        pool,
        existing["project_id"],
        principal,
        allowed=("owner", "admin", "member"),
    )

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
    set_fragments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(updates.keys()))
    params: list[object] = [dataset_id, *updates.values()]
    row = await pool.fetchrow(
        f"""
        update dataset set {set_fragments}, updated_at = now()
        where id = $1
        returning id, project_id, slug, name, description, item_count,
                  created_at, updated_at
        """,
        *params,
    )
    assert row is not None
    dataset = DatasetOut(**dict(row))
    await audit.record(
        pool,
        principal=principal,
        action="dataset.update",
        target_kind="dataset",
        target_id=dataset.id,
        payload=updates,
        request=request,
        workspace_id=workspace_id,
        project_id=dataset.project_id,
    )
    return dataset


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    request: Request,
    dataset_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    existing = await _fetch_dataset_row(pool, dataset_id)
    workspace_id = await _assert_project_role(
        pool,
        existing["project_id"],
        principal,
        allowed=("owner", "admin"),
    )
    await pool.execute(
        "update dataset set deleted_at = now(), updated_at = now() where id = $1",
        dataset_id,
    )
    await audit.record(
        pool,
        principal=principal,
        action="dataset.delete",
        target_kind="dataset",
        target_id=dataset_id,
        payload={"slug": existing["slug"]},
        request=request,
        workspace_id=workspace_id,
        project_id=existing["project_id"],
    )


@router.get("/{dataset_id}/items", response_model=DatasetItemList)
async def list_items(
    request: Request,
    dataset_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_user),
) -> DatasetItemList:
    pool: asyncpg.Pool = request.app.state.pg
    dataset = await _fetch_dataset_row(pool, dataset_id)
    await _assert_project_role(
        pool,
        dataset["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    ch = _require_clickhouse(request)

    sql = """
        select item_id, dataset_id, input, expected, metadata,
               source_run_id, source_span_id, created_at
        from dataset_item final
        where project_id = {project_id:UUID}
          and dataset_id = {dataset_id:UUID}
          and deleted_at is null
        order by created_at desc
        limit {limit:UInt32} offset {offset:UInt32}
    """
    params = {
        "project_id": str(dataset["project_id"]),
        "dataset_id": str(dataset_id),
        "limit": limit,
        "offset": offset,
    }
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("dataset items query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items = [
        DatasetItemOut(
            item_id=row["item_id"],
            dataset_id=row["dataset_id"],
            input=row["input"],
            expected=row["expected"],
            metadata=_parse_metadata(row.get("metadata")),
            source_run_id=row.get("source_run_id"),
            source_span_id=row.get("source_span_id"),
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return DatasetItemList(items=items)


@router.post(
    "/{dataset_id}/items",
    response_model=DatasetItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    request: Request,
    dataset_id: UUID,
    body: DatasetItemCreate,
    principal: Principal = Depends(require_user),
) -> DatasetItemOut:
    pool: asyncpg.Pool = request.app.state.pg
    dataset = await _fetch_dataset_row(pool, dataset_id)
    workspace_id = await _assert_project_role(
        pool,
        dataset["project_id"],
        principal,
        allowed=("owner", "admin", "member"),
    )
    ch = _require_clickhouse(request)
    org_id, workspace_id = await resolve_tenant_ids(pool, dataset["project_id"])

    item_id = uuid4()
    created_at = datetime.utcnow()
    metadata_json = json.dumps(body.metadata or {})

    try:
        await ch.insert(
            "dataset_item",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(dataset["project_id"]),
                    str(dataset_id),
                    str(item_id),
                    body.input,
                    body.expected,
                    metadata_json,
                    str(body.source_run_id) if body.source_run_id else None,
                    str(body.source_span_id) if body.source_span_id else None,
                    created_at,
                    None,
                )
            ],
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
        log.warning("dataset item insert failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    await pool.execute(
        "update dataset set item_count = item_count + 1, updated_at = now() where id = $1",
        dataset_id,
    )
    await audit.record(
        pool,
        principal=principal,
        action="dataset_item.create",
        target_kind="dataset_item",
        target_id=item_id,
        payload={"dataset_id": str(dataset_id)},
        request=request,
        workspace_id=workspace_id,
        project_id=dataset["project_id"],
    )

    return DatasetItemOut(
        item_id=item_id,
        dataset_id=dataset_id,
        input=body.input,
        expected=body.expected,
        metadata=body.metadata or {},
        source_run_id=body.source_run_id,
        source_span_id=body.source_span_id,
        created_at=created_at,
    )


@router.delete("/{dataset_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    request: Request,
    dataset_id: UUID,
    item_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    dataset = await _fetch_dataset_row(pool, dataset_id)
    workspace_id = await _assert_project_role(
        pool,
        dataset["project_id"],
        principal,
        allowed=("owner", "admin", "member"),
    )
    ch = _require_clickhouse(request)

    # Soft delete via ALTER ... UPDATE so the row stays for audit.
    sql = """
        alter table dataset_item
        update deleted_at = now64(9)
        where project_id = {project_id:UUID}
          and dataset_id = {dataset_id:UUID}
          and item_id = {item_id:UUID}
    """
    params = {
        "project_id": str(dataset["project_id"]),
        "dataset_id": str(dataset_id),
        "item_id": str(item_id),
    }
    try:
        await ch.command(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("dataset item delete failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    await pool.execute(
        "update dataset set item_count = greatest(item_count - 1, 0), "
        "updated_at = now() where id = $1",
        dataset_id,
    )
    await audit.record(
        pool,
        principal=principal,
        action="dataset_item.delete",
        target_kind="dataset_item",
        target_id=item_id,
        payload={"dataset_id": str(dataset_id)},
        request=request,
        workspace_id=workspace_id,
        project_id=dataset["project_id"],
    )


async def _fetch_dataset_row(pool: asyncpg.Pool, dataset_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, slug, name, description, item_count,
               created_at, updated_at
        from dataset
        where id = $1 and deleted_at is null
        """,
        dataset_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
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


def _parse_metadata(raw: object) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
