"""Saved views — named filter bundles applied on /runs.

A saved view bundles the runs-page filter state (status / kind /
search / window_seconds) into a named, project-scoped postgres row.
Two visibilities:
  - personal: owned by `created_by`, visible only to that user
  - shared:   `created_by` is null, visible to everyone in the project

RBAC:
  - list/get: any role (filtered server-side to "shared OR mine")
  - create personal: owner/admin/member
  - create shared:   owner/admin/member  (ownership is the project's
                     workspace, not a per-user pin)
  - update/delete personal: only the row's creator
  - update/delete shared:   owner/admin only
  - pin toggle: any user can pin/unpin their personal views; for
                shared views, pin is currently a no-op flag the
                client toggles locally. (Per-user pins on shared
                views would need a join table; v1 ships without.)

Filter shape is intentionally a free-form jsonb bag. Unknown keys
are dropped at read time so we can extend without a migration.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user

log = structlog.get_logger("langprobe.api.saved_views")

router = APIRouter(prefix="/v1/saved-views", tags=["saved-views"])

_VALID_STATUS = {"ok", "error", "running", "cancelled"}
# Intentionally duplicates ingest-api's RunKind literals rather than importing
# it (importing would add a cross-service dependency for one allowlist). The
# trailing five were added in Batch 1 (workflow/task/guardrail/evaluator/
# reranker); keep this in sync with ingest-api RunKind by hand.
_VALID_KIND = {
    "agent",
    "chain",
    "llm",
    "tool",
    "retriever",
    "embedding",
    "parser",
    "workflow",
    "task",
    "guardrail",
    "evaluator",
    "reranker",
}
_VALID_SURFACE = {"runs", "monitoring"}
_VALID_WINDOW_LABELS = {"1h", "6h", "24h", "7d"}


class SavedViewFilters(BaseModel):
    """Free-form filter bag.

    Different surfaces interpret different keys; unknown keys are
    dropped at read time. v1 schema:
      - runs:        {status, kind, search, window_seconds}
      - monitoring:  {window, model, kind}
    `window_seconds` is the canonical seconds-based window; `window` is
    a UI-friendly label ("1h" / "6h" / "24h" / "7d") used by /monitoring.
    """

    status: str | None = None
    kind: str | None = None
    search: str | None = Field(default=None, max_length=256)
    window_seconds: int | None = Field(default=None, ge=60, le=30 * 86400)
    window: str | None = Field(default=None, max_length=8)
    model: str | None = Field(default=None, max_length=128)


class SavedViewOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    surface: str
    filters: SavedViewFilters
    is_shared: bool
    pinned: bool
    sort_index: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    is_mine: bool


class SavedViewCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=120)
    surface: str = Field(default="runs")
    filters: SavedViewFilters = Field(default_factory=SavedViewFilters)
    is_shared: bool = False
    pinned: bool = False


class SavedViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filters: SavedViewFilters | None = None
    pinned: bool | None = None


def _validate_filters(f: SavedViewFilters) -> None:
    if f.status is not None and f.status not in _VALID_STATUS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"filters.status must be one of {sorted(_VALID_STATUS)} or null",
        )
    if f.kind is not None and f.kind not in _VALID_KIND:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"filters.kind must be one of {sorted(_VALID_KIND)} or null",
        )
    if f.window is not None and f.window not in _VALID_WINDOW_LABELS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"filters.window must be one of {sorted(_VALID_WINDOW_LABELS)} or null",
        )


@router.get("", response_model=list[SavedViewOut])
async def list_views(
    request: Request,
    project_id: UUID = Query(...),
    surface: str = Query(default="runs"),
    principal: Principal = Depends(require_user),
) -> list[SavedViewOut]:
    if surface not in _VALID_SURFACE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"surface must be one of {sorted(_VALID_SURFACE)}",
        )
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, name, surface, filters, is_shared, pinned,
               sort_index, created_by, created_at, updated_at
          from saved_view
         where project_id = $1
           and surface = $2
           and (is_shared = true or created_by = $3)
         order by pinned desc, sort_index asc, created_at asc
        """,
        project_id,
        surface,
        principal.user_id,
    )
    return [_view_out(r, principal.user_id) for r in rows]


@router.post(
    "",
    response_model=SavedViewOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_view(
    request: Request,
    body: SavedViewCreate,
    principal: Principal = Depends(require_user),
) -> SavedViewOut:
    if body.surface not in _VALID_SURFACE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"surface must be one of {sorted(_VALID_SURFACE)}",
        )
    _validate_filters(body.filters)

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    # Shared views have no per-user creator; personal views always carry one.
    creator = None if body.is_shared else principal.user_id

    try:
        row = await pool.fetchrow(
            """
            insert into saved_view (
                project_id, name, surface, filters, is_shared, pinned,
                created_by
            )
            values ($1, $2, $3, $4::jsonb, $5, $6, $7)
            returning id, project_id, name, surface, filters, is_shared, pinned,
                      sort_index, created_by, created_at, updated_at
            """,
            body.project_id,
            body.name,
            body.surface,
            _json.dumps(body.filters.model_dump(exclude_none=True)),
            body.is_shared,
            body.pinned and not body.is_shared,
            creator,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a view named '{body.name}' already exists in this scope",
        ) from exc
    assert row is not None

    await audit.record(
        pool,
        principal=principal,
        action="saved_view.create",
        target_kind="saved_view",
        target_id=row["id"],
        payload={
            "name": body.name,
            "surface": body.surface,
            "is_shared": body.is_shared,
            "filters": body.filters.model_dump(exclude_none=True),
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return _view_out(row, principal.user_id)


@router.patch("/{view_id}", response_model=SavedViewOut)
async def patch_view(
    request: Request,
    view_id: UUID,
    body: SavedViewPatch,
    principal: Principal = Depends(require_user),
) -> SavedViewOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_view(pool, view_id)
    project_id: UUID = row["project_id"]

    if row["is_shared"]:
        # shared: owner/admin only can edit
        workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    else:
        if row["created_by"] != principal.user_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "you can only edit your own personal views",
            )
        workspace_id = await _assert_project_role(
            pool, principal, project_id, ("owner", "admin", "member")
        )

    if body.filters is not None:
        _validate_filters(body.filters)

    sets: list[str] = []
    args: list[Any] = []
    if body.name is not None:
        args.append(body.name)
        sets.append(f"name = ${len(args)}")
    if body.filters is not None:
        args.append(_json.dumps(body.filters.model_dump(exclude_none=True)))
        sets.append(f"filters = ${len(args)}::jsonb")
    if body.pinned is not None:
        # shared views can't be pinned per-user in v1; silently coerce.
        coerced = bool(body.pinned) and not row["is_shared"]
        args.append(coerced)
        sets.append(f"pinned = ${len(args)}")

    if not sets:
        return _view_out(row, principal.user_id)

    args.append(view_id)
    try:
        updated = await pool.fetchrow(
            f"""
            update saved_view
               set {", ".join(sets)}
             where id = ${len(args)}
            returning id, project_id, name, surface, filters, is_shared, pinned,
                      sort_index, created_by, created_at, updated_at
            """,
            *args,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "name conflicts with another view in this scope",
        ) from exc
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="saved_view.update",
        target_kind="saved_view",
        target_id=view_id,
        payload={"fields": [s.split(" ")[0] for s in sets]},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _view_out(updated, principal.user_id)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(
    request: Request,
    view_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_view(pool, view_id)
    project_id: UUID = row["project_id"]

    if row["is_shared"]:
        workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    else:
        if row["created_by"] != principal.user_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "you can only delete your own personal views",
            )
        workspace_id = await _assert_project_role(
            pool, principal, project_id, ("owner", "admin", "member")
        )

    await pool.execute("delete from saved_view where id = $1", view_id)
    await audit.record(
        pool,
        principal=principal,
        action="saved_view.delete",
        target_kind="saved_view",
        target_id=view_id,
        payload={"name": row["name"], "is_shared": row["is_shared"]},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_view(pool: asyncpg.Pool, view_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, name, surface, filters, is_shared, pinned,
               sort_index, created_by, created_at, updated_at
          from saved_view
         where id = $1
        """,
        view_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved view not found")
    return row


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


def _coerce_filters(raw: Any) -> SavedViewFilters:
    if isinstance(raw, str):
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return SavedViewFilters()
    elif isinstance(raw, dict):
        data = raw
    else:
        return SavedViewFilters()
    if not isinstance(data, dict):
        return SavedViewFilters()
    # Drop unknown keys quietly so v2 clients can't poison v1 reads.
    keep = {
        k: v
        for k, v in data.items()
        if k in {"status", "kind", "search", "window_seconds", "window", "model"}
    }
    try:
        return SavedViewFilters(**keep)
    except Exception:  # noqa: BLE001
        return SavedViewFilters()


def _view_out(row: asyncpg.Record, current_user: UUID) -> SavedViewOut:
    surface_val = row["surface"] if "surface" in row.keys() else "runs"
    return SavedViewOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        surface=str(surface_val or "runs"),
        filters=_coerce_filters(row["filters"]),
        is_shared=bool(row["is_shared"]),
        pinned=bool(row["pinned"]),
        sort_index=int(row["sort_index"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_mine=row["created_by"] == current_user,
    )
