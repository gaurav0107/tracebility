"""Agent tool handlers — transport-agnostic logic behind the MCP tools.

These are the functions the MCP server and the agent-view HTTP endpoint both
call. Unit-tested with a fake ClickHouse client so the logic (queries shaped
right, projections applied, token budget honored) is covered without the MCP
transport or live services.
"""

from __future__ import annotations

import pytest
from langprobe_api.agent.tools import find_failed_runs, get_run_agent_view


class _FakeCH:
    def __init__(self, *, runs=None, run=None, spans=None):
        self._runs = runs or []
        self._run = run
        self._spans = spans or []
        self.queries: list[str] = []

    async def query(self, sql, parameters=None):
        self.queries.append(sql)
        if "status = 'error'" in sql or "status='error'" in sql:
            return self._runs
        if "from run" in sql:
            return [self._run] if self._run else []
        if "from span" in sql:
            return self._spans
        return []


@pytest.mark.asyncio
async def test_find_failed_runs_returns_compact_rows():
    ch = _FakeCH(runs=[
        {"run_id": "r1", "name": "loop", "error_kind": "ToolError",
         "error_message": "500", "start_time": "2026-06-29T00:00:00Z"},
    ])
    out = await find_failed_runs(ch, "proj-1", limit=10)
    assert len(out) == 1
    assert out[0]["run_id"] == "r1"
    assert out[0]["error_message"] == "500"
    # queried the error-filtered run table
    assert any("error" in q for q in ch.queries)


@pytest.mark.asyncio
async def test_get_run_agent_view_projects_within_budget():
    ch = _FakeCH(
        run={"run_id": "r1", "name": "loop", "status": "error",
             "kind": "chain", "error_kind": "ToolError",
             "error_message": "search 500"},
        spans=[
            {"span_id": "s1", "kind": "llm", "status": "ok",
             "name": "plan", "inputs": "hi", "outputs": "ok", "latency_ms": 50},
            {"span_id": "s2", "kind": "tool", "status": "error",
             "name": "search", "inputs": "q", "outputs": "boom",
             "latency_ms": 200},
        ],
    )
    view = await get_run_agent_view(ch, "proj-1", "r1", token_budget=2000)
    assert view["run_id"] == "r1"
    assert view["status"] == "error"
    assert "search 500" in view["summary"]
    assert "compact_text" in view
    assert view["est_tokens"] <= 2000
    # errored span surfaced
    assert any(s["span_id"] == "s2" for s in view["spans"])


@pytest.mark.asyncio
async def test_get_run_agent_view_missing_run_returns_none():
    ch = _FakeCH(run=None, spans=[])
    view = await get_run_agent_view(ch, "proj-1", "missing", token_budget=2000)
    assert view is None
