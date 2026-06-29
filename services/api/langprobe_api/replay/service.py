"""Replay service — shared core for the HTTP endpoint and the MCP tool.

Both `POST /v1/runs/{run_id}/replay` and the MCP `replay_run` tool call
`run_span_replay`. Keeping the span load, gateway dispatch, orchestration, diff,
and replay_run persistence in one place avoids forking the dispatch closure
(DRY) and means both surfaces share the same determinism contract.

The LLM dispatch goes through the gateway (Studio's path). Tool / retriever
spans can't re-dispatch through the LLM gateway in Phase 0 — they return a loud
error outcome so the diff escalates to tool_io_missing.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import HTTPException

from ..clickhouse_client import ClickHouseQuery
from .diff import ReplayDiff, compute_replay_diff
from .executor import DispatchOutcome, ReplayEdit, apply_llm_edits, execute_replay
from .record import REPLAY_RUN_COLUMNS, build_replay_run_row

log = structlog.get_logger("langprobe.replay.service")

_MAX_DISPATCH_TOKENS = 1024


async def load_spans(
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


async def capturable_span_ids(
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


def build_gateway_dispatch(pool: asyncpg.Pool, project_id: UUID, replay_run_id: UUID):
    """A dispatch closure that re-executes one edited LLM span via the gateway."""

    async def dispatch(
        span: dict[str, Any], span_edits: list[ReplayEdit]
    ) -> DispatchOutcome:
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
        from ..routers import playground as playground_module

        try:
            provider = playground_module._resolve_provider(model)  # type: ignore[attr-defined]
        except HTTPException as exc:
            return DispatchOutcome("", "", 0.0, 0, 0, 0, error=f"provider routing: {exc.detail}")

        bare_model = model if model.startswith(provider + "/") else f"{provider}/{model}"
        t0 = time.monotonic()
        try:
            result = await gateway_dispatch(
                pool,
                project_id=project_id,
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

    return dispatch


async def run_span_replay(
    pool: asyncpg.Pool,
    ch: ClickHouseQuery,
    *,
    project_id: UUID,
    run_id: UUID,
    edits: list[ReplayEdit],
    replay_run_id: UUID,
    started_at: Any,
    finished_at: Any,
) -> ReplayDiff | None:
    """Load, replay, diff, and persist. Returns None when the run has no spans."""
    spans = await load_spans(ch, project_id, run_id)
    capturable = await capturable_span_ids(ch, project_id, run_id)
    if not spans:
        return None

    dispatch = build_gateway_dispatch(pool, project_id, replay_run_id)
    plan = await execute_replay(
        spans, edits, dispatch=dispatch, capturable_span_ids=capturable
    )
    diff = compute_replay_diff(
        plan.pairs,
        edited_span_ids=plan.edited_span_ids,
        missing_capture_span_ids=plan.missing_capture_span_ids,
    )

    row = build_replay_run_row(
        diff,
        project_id=project_id,
        replay_run_id=replay_run_id,
        original_run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    try:
        await ch.insert("replay_run", [row], column_names=list(REPLAY_RUN_COLUMNS))
    except Exception as exc:  # noqa: BLE001
        # Derived store — never fail the action on the replay_run write (ER-23).
        log.warning(
            "replay_run insert failed", replay_run_id=str(replay_run_id), error=str(exc)
        )
    return diff
