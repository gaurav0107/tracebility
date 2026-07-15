"""Studio canvas — branches of captured runs (parity loop #4 item #9).

A `studio_branch` is the postgres-side record of "I took run X, picked
span Y as the branch point, and want to edit fields A/B/C". Studio is
the round-trip surface between Replay (the captured truth) and Prompts
(the candidate revision). The actual replay-runner that re-executes
the branched flow with edits applied slots in next iteration; here we
ship the storage shape and the canvas wiring.

V1 honest scope:
- Branch CRUD: draft -> replayed -> promoted lifecycle.
- Replay action: synthesizes a `diff_summary` describing what the
  branch changes (prompt/model/temperature/tool_args per target span),
  flips status to `replayed`, stamps `replayed_at`. Does NOT yet
  produce a new ClickHouse run; `replay_run_id` stays null until the
  runner lands. This is the same shape the comparisons runner uses
  (built-in stand-in today, real LLM runner tomorrow).
- Promote action: flips status to `promoted`. Wiring into Prompts
  revisions also lands next iteration.

ER-23: never silent-drop. If the source run is missing in ClickHouse
the branch row stays — the UI surfaces "source run missing" instead
of cascading delete.
"""

from __future__ import annotations

import json as _json
import time
import uuid as _uuid
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
from . import playground as playground_module

log = structlog.get_logger("langprobe.api.studio")

router = APIRouter(prefix="/v1/studio", tags=["studio"])

_ALLOWED_FIELDS = {"prompt", "model", "temperature", "tool_args"}
_BRANCH_STATUSES = {"draft", "replayed", "failed", "promoted"}


class StudioEdit(BaseModel):
    target_span_id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=32)
    value: Any = None


class StudioBranchOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    description: str | None
    source_run_id: str
    source_span_id: str | None
    edits: list[StudioEdit]
    replay_run_id: str | None
    status: str
    diff_summary: str | None
    created_at: datetime
    updated_at: datetime
    replayed_at: datetime | None


class StudioBranchCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    source_run_id: str = Field(min_length=1, max_length=128)
    source_span_id: str | None = Field(default=None, max_length=128)
    edits: list[StudioEdit] = Field(default_factory=list, max_length=64)


class StudioBranchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    edits: list[StudioEdit] | None = Field(default=None, max_length=64)


class StudioBranchList(BaseModel):
    items: list[StudioBranchOut]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/branches", response_model=StudioBranchList)
async def list_branches(
    request: Request,
    project_id: UUID = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(require_user),
) -> StudioBranchList:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, name, description, source_run_id,
               source_span_id, edits, replay_run_id, status,
               diff_summary, created_at, updated_at, replayed_at
          from studio_branch
         where project_id = $1
         order by created_at desc
         limit $2
        """,
        project_id,
        limit,
    )
    return StudioBranchList(items=[_branch_out(r) for r in rows])


@router.post(
    "/branches",
    response_model=StudioBranchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_branch(
    request: Request,
    body: StudioBranchCreate,
    principal: Principal = Depends(require_user),
) -> StudioBranchOut:
    _validate_edits(body.edits)

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    edits_json = _json.dumps([e.model_dump() for e in body.edits])

    row = await pool.fetchrow(
        """
        insert into studio_branch (
            project_id, name, description, source_run_id, source_span_id,
            edits, status, author_id
        )
        values ($1, $2, $3, $4, $5, $6::jsonb, 'draft', $7)
        returning id, project_id, name, description, source_run_id,
                  source_span_id, edits, replay_run_id, status,
                  diff_summary, created_at, updated_at, replayed_at
        """,
        body.project_id,
        body.name,
        body.description,
        body.source_run_id,
        body.source_span_id,
        edits_json,
        principal.user_id,
    )
    assert row is not None

    await audit.record(
        pool,
        principal=principal,
        action="studio_branch.create",
        target_kind="studio_branch",
        target_id=row["id"],
        payload={
            "name": body.name,
            "source_run_id": body.source_run_id,
            "source_span_id": body.source_span_id,
            "edit_count": len(body.edits),
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return _branch_out(row)


@router.get("/branches/{branch_id}", response_model=StudioBranchOut)
async def get_branch(
    request: Request,
    branch_id: UUID,
    principal: Principal = Depends(require_user),
) -> StudioBranchOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_branch(pool, branch_id)
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    return _branch_out(row)


@router.patch("/branches/{branch_id}", response_model=StudioBranchOut)
async def patch_branch(
    request: Request,
    branch_id: UUID,
    body: StudioBranchPatch,
    principal: Principal = Depends(require_user),
) -> StudioBranchOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_branch(pool, branch_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )

    if row["status"] not in ("draft", "failed") and body.edits is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "edits are frozen once the branch has been replayed",
        )

    if body.edits is not None:
        _validate_edits(body.edits)

    sets: list[str] = []
    args: list[Any] = []
    if body.name is not None:
        args.append(body.name)
        sets.append(f"name = ${len(args)}")
    if body.description is not None:
        args.append(body.description)
        sets.append(f"description = ${len(args)}")
    if body.edits is not None:
        args.append(_json.dumps([e.model_dump() for e in body.edits]))
        sets.append(f"edits = ${len(args)}::jsonb")

    if not sets:
        return _branch_out(row)

    args.append(branch_id)
    updated = await pool.fetchrow(
        f"""
        update studio_branch
           set {", ".join(sets)}
         where id = ${len(args)}
        returning id, project_id, name, description, source_run_id,
                  source_span_id, edits, replay_run_id, status,
                  diff_summary, created_at, updated_at, replayed_at
        """,
        *args,
    )
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="studio_branch.update",
        target_kind="studio_branch",
        target_id=branch_id,
        payload={"fields": sorted([s.split(" ")[0] for s in sets])},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _branch_out(updated)


@router.delete(
    "/branches/{branch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_branch(
    request: Request,
    branch_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_branch(pool, branch_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    await pool.execute("delete from studio_branch where id = $1", branch_id)
    await audit.record(
        pool,
        principal=principal,
        action="studio_branch.delete",
        target_kind="studio_branch",
        target_id=branch_id,
        payload={},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )


@router.post(
    "/branches/{branch_id}/replay",
    response_model=StudioBranchOut,
)
async def replay_branch(
    request: Request,
    branch_id: UUID,
    principal: Principal = Depends(require_user),
) -> StudioBranchOut:
    """Replay a branched run with edits applied.

    Real runner. Steps:
      1) Resolve the source run + branch-point span from ClickHouse.
      2) Apply edits (prompt / model / temperature / tool_args) to the
         span being replaced. v1 replays the **single** branch-point
         span (or root) — multi-span replay is a future iteration.
      3) Dispatch the LLM via playground._dispatch (which honors the
         workspace LLM credential store with env fallback).
      4) Write a new run + span to ClickHouse with sdk='studio' so it
         shows up under /runs.
      5) Stamp `replay_run_id` on the branch and flip status to
         'replayed'. `diff_summary` records the divergence.

    A branch with zero edits replays the source verbatim (still useful
    as a smoke test that captures match the original).
    """
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_branch(pool, branch_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )
    if row["status"] not in ("draft", "replayed", "failed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot replay a branch in status={row['status']}",
        )

    edits = _parse_edits(row["edits"])
    edit_summary = _summarize_edits(edits) or "no edits applied"

    # Execute the replay; failures are recorded on the branch row but
    # don't crash the request — operators see the failure and retry.
    new_run_id, dispatch_summary, dispatch_error = await _execute_replay(
        request,
        pool=pool,
        project_id=project_id,
        workspace_id=workspace_id,
        branch_id=branch_id,
        source_run_id=row["source_run_id"],
        source_span_id=row["source_span_id"],
        edits=edits,
    )

    if dispatch_error is not None:
        diff_summary = f"replay failed: {dispatch_error}; {edit_summary}"
        new_status = "failed"
    elif dispatch_summary:
        diff_summary = f"{edit_summary}; {dispatch_summary}"
        new_status = "replayed"
    else:
        diff_summary = edit_summary
        new_status = "replayed"

    updated = await pool.fetchrow(
        """
        update studio_branch
           set status = $5,
               diff_summary = $2,
               replay_run_id = coalesce($4, replay_run_id),
               replayed_at = $3
         where id = $1
        returning id, project_id, name, description, source_run_id,
                  source_span_id, edits, replay_run_id, status,
                  diff_summary, created_at, updated_at, replayed_at
        """,
        branch_id,
        diff_summary,
        datetime.now(UTC),
        new_run_id,
        new_status,
    )
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="studio_branch.replay",
        target_kind="studio_branch",
        target_id=branch_id,
        payload={
            "edit_count": len(edits),
            "diff_summary": diff_summary,
            "replay_run_id": new_run_id,
            "ok": dispatch_error is None,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _branch_out(updated)


@router.post(
    "/branches/{branch_id}/promote",
    response_model=StudioBranchOut,
)
async def promote_branch(
    request: Request,
    branch_id: UUID,
    principal: Principal = Depends(require_user),
) -> StudioBranchOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_branch(pool, branch_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )
    if row["status"] != "replayed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "branch must be replayed before it can be promoted",
        )

    updated = await pool.fetchrow(
        """
        update studio_branch
           set status = 'promoted'
         where id = $1
        returning id, project_id, name, description, source_run_id,
                  source_span_id, edits, replay_run_id, status,
                  diff_summary, created_at, updated_at, replayed_at
        """,
        branch_id,
    )
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="studio_branch.promote",
        target_kind="studio_branch",
        target_id=branch_id,
        payload={},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return _branch_out(updated)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_edits(edits: list[StudioEdit]) -> None:
    for idx, edit in enumerate(edits):
        if edit.field not in _ALLOWED_FIELDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"edits[{idx}].field must be one of {sorted(_ALLOWED_FIELDS)}",
            )
        if edit.field == "temperature":
            try:
                t = float(edit.value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"edits[{idx}].value must be a number for temperature",
                ) from exc
            if not 0.0 <= t <= 2.0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"edits[{idx}].value must be in [0.0, 2.0] for temperature",
                )
        elif edit.field in ("prompt", "model"):
            if not isinstance(edit.value, str) or not edit.value.strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"edits[{idx}].value must be a non-empty string for {edit.field}",
                )
        elif edit.field == "tool_args":
            if not isinstance(edit.value, (dict, list)):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"edits[{idx}].value must be a JSON object or array for tool_args",
                )


def _summarize_edits(edits: list[StudioEdit]) -> str:
    if not edits:
        return ""
    parts: list[str] = []
    for edit in edits:
        target = (edit.target_span_id or "")[:8] or "—"
        if edit.field == "model":
            parts.append(f"model@{target} → {edit.value}")
        elif edit.field == "temperature":
            parts.append(f"temp@{target} → {edit.value}")
        elif edit.field == "prompt":
            length = len(str(edit.value)) if edit.value is not None else 0
            parts.append(f"prompt@{target} ({length} chars)")
        elif edit.field == "tool_args":
            keys = ", ".join(sorted(edit.value.keys())) if isinstance(edit.value, dict) else "[...]"
            parts.append(f"tool_args@{target} ({keys})")
        else:
            parts.append(f"{edit.field}@{target}")
    return f"{len(edits)} edit{'s' if len(edits) != 1 else ''}: {'; '.join(parts)}"


async def _fetch_branch(pool: asyncpg.Pool, branch_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, name, description, source_run_id,
               source_span_id, edits, replay_run_id, status,
               diff_summary, created_at, updated_at, replayed_at
          from studio_branch
         where id = $1
        """,
        branch_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "branch not found")
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


def _branch_out(row: asyncpg.Record) -> StudioBranchOut:
    return StudioBranchOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"],
        source_run_id=row["source_run_id"],
        source_span_id=row["source_span_id"],
        edits=_parse_edits(row["edits"]),
        replay_run_id=row["replay_run_id"],
        status=row["status"],
        diff_summary=row["diff_summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        replayed_at=row["replayed_at"],
    )


async def _execute_replay(
    request: Request,
    *,
    pool: asyncpg.Pool,
    project_id: UUID,
    workspace_id: UUID,
    branch_id: UUID,
    source_run_id: str,
    source_span_id: str | None,
    edits: list[StudioEdit],
) -> tuple[str | None, str, str | None]:
    """Re-execute the source run with edits applied.

    Returns ``(new_run_id, dispatch_summary, error)``. On success
    the new run is written to ClickHouse and ``new_run_id`` is the
    UUID we wrote. On failure ``new_run_id`` is None, ``error``
    carries a short reason, and the branch row records the failure
    in its diff_summary.

    v1 scope: replays the **single** branch-point span (or the run's
    root span if `source_span_id` is None). Multi-span replay is a
    future iteration. Tool calls inside the replayed span are NOT
    re-executed — we just record the new prompt + new model output.
    The Replay panel on /runs already surfaces the captures so an
    operator can see how the boundary I/O would have differed.
    """
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        return None, "", "clickhouse not configured"

    org_id, _ = await resolve_tenant_ids(pool, project_id)

    # 1) Resolve the source span we're replacing. Precedence: the
    #    branch's explicit branch point; else the single span all edits
    #    target (the operator said "replay THIS span" by editing it);
    #    else the run's first llm span.
    effective_span_id = source_span_id
    if effective_span_id is None:
        edit_targets = {e.target_span_id for e in edits if e.target_span_id}
        if len(edit_targets) == 1:
            effective_span_id = next(iter(edit_targets))
    source_data = await _resolve_source_span(ch, project_id, source_run_id, effective_span_id)
    if source_data is None:
        return None, "", "source span not found in clickhouse"

    base_prompt = source_data.get("inputs") or ""
    base_model = source_data.get("model") or ""
    base_temperature = source_data.get("temperature")

    # 2) Apply edits. v1 only honors edits whose target_span_id
    #    matches our branch-point span (or the root if no target
    #    was given). Other edits are recorded in the metadata but
    #    don't fire — keeps the contract honest.
    target_id = effective_span_id or str(source_data.get("span_id") or "")
    new_prompt = base_prompt
    new_model = base_model
    new_temperature = base_temperature
    applied: list[str] = []
    skipped: list[str] = []
    for edit in edits:
        if edit.target_span_id and edit.target_span_id != target_id:
            skipped.append(f"{edit.field}@{edit.target_span_id[:8]}")
            continue
        if edit.field == "prompt":
            new_prompt = str(edit.value or "")
            applied.append("prompt")
        elif edit.field == "model":
            new_model = str(edit.value or "")
            applied.append("model")
        elif edit.field == "temperature":
            try:
                new_temperature = float(edit.value)
                applied.append("temperature")
            except (TypeError, ValueError):
                skipped.append("temperature(invalid)")
        elif edit.field == "tool_args":
            # Tool args don't dispatch in v1 — we still record them
            # in the new run's metadata so the diff is visible.
            applied.append("tool_args(metadata-only)")

    if not new_prompt.strip():
        return None, "", "rendered prompt is empty"

    if not new_model.strip():
        kind = str(source_data.get("kind") or "unknown")
        return (
            None,
            "",
            f"span '{source_data.get('name')}' ({kind}) has no model to replay — "
            "add a model edit or branch from an llm span",
        )

    # 3) Resolve provider from the model prefix.
    try:
        provider = playground_module._resolve_provider(  # type: ignore[attr-defined]
            new_model
        )
    except HTTPException as exc:
        return None, "", f"provider routing failed: {exc.detail}"

    # 4) Dispatch the LLM via the gateway. Errors land on the branch as
    #    `replay failed: <reason>`; the branch still flips to `replayed`
    #    so the operator can see the failure inline.
    started = time.monotonic()
    bare_model = new_model
    if not bare_model.startswith(provider + "/"):
        bare_model = f"{provider}/{bare_model}"
    from ..llm import DispatchError, Message
    from ..llm import dispatch as gateway_dispatch

    try:
        result = await gateway_dispatch(
            pool,
            project_id=project_id,
            surface="studio",
            surface_ref_id=branch_id,
            model=bare_model,
            messages=[Message(role="user", content=new_prompt)],
            temperature=new_temperature,
            max_tokens=1024,
        )
    except DispatchError as exc:
        log.warning(
            "studio replay dispatch failed",
            branch_id=str(branch_id),
            provider=provider,
            code=exc.code,
            detail=exc.detail,
        )
        return None, "", f"[{exc.code}] {exc.detail}"

    latency_ms = int((time.monotonic() - started) * 1000)
    new_run_id = str(_uuid.uuid4())
    new_span_id = str(_uuid.uuid4())
    output_text = result.text
    prompt_tokens = int(result.prompt_tokens or 0)
    completion_tokens = int(result.completion_tokens or 0)
    total_tokens = prompt_tokens + completion_tokens

    # 5) Write the new run + its single replay span to ClickHouse.
    now = datetime.now(UTC)
    inputs_json = _json.dumps({"prompt": new_prompt})
    outputs_json = _json.dumps({"output": output_text})
    metadata = {
        "studio": True,
        "branch_id": str(branch_id),
        "source_run_id": source_run_id,
        "source_span_id": target_id,
        "applied_edits": applied,
        "skipped_edits": skipped,
    }
    try:
        await ch.insert(
            "run",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    new_run_id,
                    None,
                    "studio_replay",
                    "llm",
                    "ok",
                    "studio",
                    now,
                    now,
                    now,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    0,
                    str(branch_id),
                    "",
                    ["studio", "replay"],
                    _json.dumps(metadata),
                    "",
                    "",
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "parent_run_id",
                "name",
                "kind",
                "status",
                "sdk",
                "start_time",
                "end_time",
                "received_at",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "session_id",
                "user_id",
                "tags",
                "metadata",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
        await ch.insert(
            "span",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    new_run_id,
                    new_span_id,
                    None,
                    f"replay:{new_model}",
                    "llm",
                    "ok",
                    now,
                    now,
                    now,
                    new_model,
                    new_temperature,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    0,
                    _json.dumps(metadata),
                    "",
                    "",
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "span_id",
                "parent_span_id",
                "name",
                "kind",
                "status",
                "start_time",
                "end_time",
                "received_at",
                "model",
                "temperature",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "attributes",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "studio replay clickhouse write failed",
            branch_id=str(branch_id),
            error=str(exc),
        )
        return None, "", f"clickhouse write failed: {exc}"

    summary_parts = [
        f"replay_run_id={new_run_id[:8]}…",
        f"latency={latency_ms}ms",
    ]
    if applied:
        summary_parts.append(f"applied=[{', '.join(applied)}]")
    if skipped:
        summary_parts.append(f"skipped=[{', '.join(skipped)}]")
    return new_run_id, " ".join(summary_parts), None


async def _resolve_source_span(
    ch: ClickHouseQuery,
    project_id: UUID,
    source_run_id: str,
    source_span_id: str | None,
) -> dict[str, Any] | None:
    """Find the span we're replacing in the source run.

    With a span_id, we look it up directly. Without one, we pick the
    run's first **llm** span — replay dispatches a model call, and on
    agent/chain runs the root span carries no model, so "replay @root"
    means "replay the first LLM boundary". Runs with no llm span fall
    back to the root so the caller can surface an honest error.
    """
    try:
        if source_span_id:
            rows = await ch.query(
                """
                select span_id, name, kind, model, temperature, inputs, outputs
                  from span final
                 where project_id = {project_id:UUID}
                   and run_id = {run_id:UUID}
                   and toString(span_id) = {span_id:String}
                 limit 1
                """,
                parameters={
                    "project_id": str(project_id),
                    "run_id": source_run_id,
                    "span_id": source_span_id,
                },
            )
        else:
            rows = await ch.query(
                """
                select span_id, name, kind, model, temperature, inputs, outputs
                  from span final
                 where project_id = {project_id:UUID}
                   and run_id = {run_id:UUID}
                   and kind = 'llm'
                   and model != ''
                 order by start_time asc
                 limit 1
                """,
                parameters={
                    "project_id": str(project_id),
                    "run_id": source_run_id,
                },
            )
            if not rows:
                rows = await ch.query(
                    """
                    select span_id, name, kind, model, temperature, inputs, outputs
                      from span final
                     where project_id = {project_id:UUID}
                       and run_id = {run_id:UUID}
                       and parent_span_id is null
                     order by start_time asc
                     limit 1
                    """,
                    parameters={
                        "project_id": str(project_id),
                        "run_id": source_run_id,
                    },
                )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "studio source-span resolve failed",
            run_id=source_run_id,
            error=str(exc),
        )
        return None
    if not rows:
        return None
    return rows[0]


def _parse_edits(raw: Any) -> list[StudioEdit]:
    data = raw
    if isinstance(raw, str):
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out: list[StudioEdit] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(StudioEdit(**entry))
        except Exception:  # noqa: BLE001
            continue
    return out
