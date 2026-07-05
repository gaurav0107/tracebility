"""Agent tool handlers — transport-agnostic logic behind the MCP tools.

Each function is the logic of one agent-callable tool. The MCP server
(`mcp_server.py`) and the agent-view HTTP endpoints both call these, so the
behaviour is defined and tested in one place, independent of transport.

Reads return token-budgeted projections (see `projections.py`) — an agent gets
the salient slice, not a raw trace dump.
"""

from __future__ import annotations

from typing import Any

from ..clickhouse_client import ClickHouseQuery
from .projections import compact_text, project_run


async def find_failed_runs(
    ch: ClickHouseQuery,
    project_id: str,
    *,
    limit: int = 20,
    window_seconds: int = 86_400,
) -> list[dict[str, Any]]:
    """Most recent errored runs for a project — the agent's entry point.

    Compact rows only (id + error), so an agent can scan many and then pull a
    full agent-view of the one it cares about.
    """
    rows = await ch.query(
        """
        select toString(run_id) as run_id, name, error_kind, error_message,
               start_time
          from run final
         where project_id = {project_id:UUID}
           and status = 'error'
           and start_time >= now64(9) - toIntervalSecond({window:UInt32})
         order by start_time desc
         limit {limit:UInt32}
        """,
        parameters={
            "project_id": project_id,
            "window": window_seconds,
            "limit": limit,
        },
    )
    return [
        {
            "run_id": str(r.get("run_id") or ""),
            "name": str(r.get("name") or ""),
            "error_kind": str(r.get("error_kind") or ""),
            "error_message": str(r.get("error_message") or ""),
            "started_at": str(r.get("start_time") or ""),
        }
        for r in rows
    ]


async def get_run_agent_view(
    ch: ClickHouseQuery,
    project_id: str,
    run_id: str,
    *,
    token_budget: int = 2000,
) -> dict[str, Any] | None:
    """Token-budgeted, LLM-legible view of one run. None if the run is absent."""
    run_rows = await ch.query(
        """
        select toString(run_id) as run_id, name, status, kind,
               error_kind, error_message
          from run final
         where project_id = {project_id:UUID}
           and run_id = {run_id:UUID}
         limit 1
        """,
        parameters={"project_id": project_id, "run_id": run_id},
    )
    if not run_rows:
        return None
    run = run_rows[0]

    span_rows = await ch.query(
        """
        select toString(span_id) as span_id, kind, status, name,
               inputs, outputs,
               toInt64(ifNull(dateDiff('millisecond', start_time, end_time), 0))
                   as latency_ms
          from span final
         where project_id = {project_id:UUID}
           and run_id = {run_id:UUID}
         order by start_time asc
        """,
        parameters={"project_id": project_id, "run_id": run_id},
    )

    projected = project_run(dict(run), list(span_rows), token_budget=token_budget)
    return {
        "run_id": projected.run_id,
        "name": projected.name,
        "status": projected.status,
        "error": projected.error,
        "summary": projected.summary,
        "truncated": projected.truncated,
        "est_tokens": projected.est_tokens,
        "compact_text": compact_text(projected),
        "spans": [
            {
                "span_id": s.span_id,
                "kind": s.kind,
                "name": s.name,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "inputs_preview": s.inputs_preview,
                "outputs_preview": s.outputs_preview,
                "truncated": s.truncated,
            }
            for s in projected.spans
        ],
    }
