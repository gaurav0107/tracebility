"""MCP server — the primary agent interface to langprobe.

Exposes the 2am debug loop as agent-callable tools over the Model Context
Protocol: find the broken run, read its salient slice, replay an edit, read the
diff. Every tool wraps the same handlers (`agent.tools`) and replay service the
HTTP surface uses — one logic, two transports.

Construction injects `get_ch` / `get_pool` so the server binds to the running
app's ClickHouse / Postgres without importing app state at module load (keeps it
testable with fakes). `streamable_http_app()` yields an ASGI app to mount.

SECURITY — deliberately not auto-mounted in the public app yet. MCP-over-HTTP
needs API-key auth + per-call workspace/tenant scoping before exposure; tools
currently trust the caller-supplied `project_id`. That auth pass is the next
slice; until then this is wired for stdio / local and covered by smoke tests.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from ..clickhouse_client import ClickHouseQuery
from ..replay.executor import ReplayEdit
from ..replay.record import summarize_diff
from ..replay.service import run_span_replay
from .tools import find_failed_runs, get_run_agent_view


def build_mcp_server(
    *,
    get_ch: Callable[[], ClickHouseQuery],
    get_pool: Callable[[], Any],
) -> FastMCP:
    mcp = FastMCP("langprobe")

    @mcp.tool()
    async def list_failed_runs(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recent errored runs for a project. The agent's entry point: scan
        these, then pull get_run on the one to debug."""
        return await find_failed_runs(get_ch(), project_id, limit=limit)

    @mcp.tool()
    async def get_run(
        project_id: str, run_id: str, token_budget: int = 2000
    ) -> dict[str, Any] | None:
        """Token-budgeted, LLM-legible view of one run: the salient spans,
        errored-first, with truncated I/O and a one-line verdict."""
        return await get_run_agent_view(
            get_ch(), project_id, run_id, token_budget=token_budget
        )

    @mcp.tool()
    async def replay_run(
        project_id: str, run_id: str, edits: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Replay a run with edits applied (Phase 0 span-level what-if), and
        return the diff verdict. `edits`: [{target_span_id, field, value}],
        field in prompt|model|temperature. Empty edits = re-run unchanged."""
        replay_edits = [
            ReplayEdit(
                target_span_id=str(e["target_span_id"]),
                field=str(e["field"]),
                value=e.get("value"),
            )
            for e in (edits or [])
        ]
        replay_run_id = uuid4()
        started = datetime.now(UTC)
        diff = await run_span_replay(
            get_pool(),
            get_ch(),
            project_id=project_id,
            run_id=run_id,
            edits=replay_edits,
            replay_run_id=replay_run_id,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
        if diff is None:
            return {"error": "run has no spans", "run_id": run_id}
        return {
            "replay_run_id": str(replay_run_id),
            "original_run_id": run_id,
            "determinism": diff.determinism,
            "outcome": diff.outcome,
            "span_count_total": diff.span_count_total,
            "span_count_diverged": diff.span_count_diverged,
            "summary": summarize_diff(diff),
            "deltas": [
                {
                    "span_id": d.span_id,
                    "name": d.name,
                    "diverged": d.diverged,
                    "output_changed": d.output_changed,
                    "model_changed": d.model_changed,
                    "note": d.note,
                }
                for d in diff.deltas
            ],
        }

    return mcp
