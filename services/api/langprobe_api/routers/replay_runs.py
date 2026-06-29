"""Replay execution endpoint — Phase 0 span-level what-if.

``POST /v1/runs/{run_id}/replay`` re-dispatches the edited span(s) live, holds
the rest at captured values, computes the diff, and persists a ``replay_run``
row (schema 0003). The response carries the diff + an agent-legible summary
(the same summary the Phase 1 MCP tool will return).

Thin by design: the orchestration (`execute_replay`), diff (`compute_replay_diff`),
edit application (`apply_llm_edits`), and record building (`build_replay_run_row`)
are all unit-tested in ``langprobe_api.replay``. This module is the I/O wiring:
read spans from ClickHouse, build the real LLM dispatch via the gateway (same
path Studio uses), write the result back.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..replay.diff import compute_replay_diff
from ..replay.executor import (
    DispatchOutcome,
    ReplayEdit,
    apply_llm_edits,
    execute_replay,
)
from ..replay.record import REPLAY_RUN_COLUMNS, build_replay_run_row, summarize_diff

log = structlog.get_logger("langprobe.api.replay_runs")

runs_router = APIRouter(prefix="/v1/runs", tags=["replays"])

_MAX_DISPATCH_TOKENS = 1024


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


async def _load_spans(
    ch: ClickHouseQuery, project_id: UUID, run_id: UUID
) -> list[dict[str, Any]]:
    rows = await ch.query(
        """
        select toString(span_id) as span_id,
               name, kind, model, temperature, inputs, outputs,
               cost_usd,
               toInt64(ifNull(dateDiff('millisecond', start_time, end_time), 0))
                   as latency_ms
          from span final
         where project_id = {project_id:UUID}
           and run_id = {run_id:UUID}
         order by start_time asc
        """,
        parameters={"project_id": str(project_id), "run_id": str(run_id)},
    )
    return list(rows)


async def _capturable_span_ids(
    ch: ClickHouseQuery, project_id: UUID, run_id: UUID
) -> set[str]:
    rows = await ch.query(
        """
        select distinct toString(span_id) as span_id
          from replay_capture final
         where project_id = {project_id:UUID}
           and run_id = {run_id:UUID}
        """,
        parameters={"project_id": str(project_id), "run_id": str(run_id)},
    )
    return {str(r["span_id"]) for r in rows}


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

    try:
        spans = await _load_spans(ch, body.project_id, run_id)
        capturable = await _capturable_span_ids(ch, body.project_id, run_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("replay span load failed", run_id=str(run_id), error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable"
        ) from exc

    if not spans:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run has no spans")

    edits = [
        ReplayEdit(target_span_id=e.target_span_id, field=e.field, value=e.value)
        for e in body.edits
    ]
    replay_run_id = uuid4()
    started_at = datetime.now(UTC)

    # Real dispatch: re-execute one edited LLM span via the gateway. Tool /
    # retriever spans can't re-dispatch through the LLM gateway in Phase 0 —
    # return a loud error outcome so the diff escalates to tool_io_missing.
    async def dispatch(span: dict[str, Any], span_edits: list[ReplayEdit]) -> DispatchOutcome:
        if (span.get("kind") or "") != "llm":
            return DispatchOutcome(
                "", "", 0.0, 0, 0, 0,
                error=f"Phase 0 replays llm spans only; got kind={span.get('kind')!r}",
            )
        prompt, model, temperature, _applied, _skipped = apply_llm_edits(
            base_inputs=str(span.get("inputs") or ""),
            base_model=str(span.get("model") or ""),
            base_temperature=span.get("temperature"),
            edits=span_edits,
        )
        if not prompt.strip():
            return DispatchOutcome("", "", 0.0, 0, 0, 0, error="rendered prompt is empty")

        from ..llm import DispatchError, Message
        from ..llm import dispatch as gateway_dispatch
        from . import playground as playground_module

        try:
            provider = playground_module._resolve_provider(model)  # type: ignore[attr-defined]
        except HTTPException as exc:
            return DispatchOutcome("", "", 0.0, 0, 0, 0, error=f"provider routing: {exc.detail}")

        bare_model = model if model.startswith(provider + "/") else f"{provider}/{model}"
        t0 = time.monotonic()
        try:
            result = await gateway_dispatch(
                pool,
                project_id=body.project_id,
                surface="replay",
                surface_ref_id=replay_run_id,
                model=bare_model,
                messages=[Message(role="user", content=prompt)],
                temperature=temperature,
                max_tokens=_MAX_DISPATCH_TOKENS,
            )
        except DispatchError as exc:
            return DispatchOutcome("", "", 0.0, 0, 0, 0, error=f"[{exc.code}] {exc.detail}")
        latency_ms = int((time.monotonic() - t0) * 1000)
        return DispatchOutcome(
            outputs=result.text,
            model=bare_model,
            cost_usd=0.0,
            latency_ms=latency_ms,
            prompt_tokens=int(result.prompt_tokens or 0),
            completion_tokens=int(result.completion_tokens or 0),
        )

    plan = await execute_replay(
        spans, edits, dispatch=dispatch, capturable_span_ids=capturable
    )
    diff = compute_replay_diff(
        plan.pairs,
        edited_span_ids=plan.edited_span_ids,
        missing_capture_span_ids=plan.missing_capture_span_ids,
    )

    finished_at = datetime.now(UTC)
    row = build_replay_run_row(
        diff,
        project_id=body.project_id,
        replay_run_id=replay_run_id,
        original_run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    try:
        await ch.insert("replay_run", [row], column_names=list(REPLAY_RUN_COLUMNS))
    except Exception as exc:  # noqa: BLE001
        # The diff is computed and returned regardless; the replay_run record is
        # a derived store (ER-23: never fail the user action on a derived write).
        log.warning(
            "replay_run insert failed",
            replay_run_id=str(replay_run_id),
            error=str(exc),
        )

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
