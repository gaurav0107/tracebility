"""Smoke test for the MCP server: tools register and execute against fakes.

Full transport (streamable HTTP / stdio) isn't exercised here — that needs a
client. This proves the server constructs, the three debug-loop tools are
registered with the right names, and a tool actually runs through to the shared
handler.
"""

from __future__ import annotations

import pytest
from langprobe_api.agent.mcp_server import build_mcp_server


class _FakeCH:
    def __init__(self, *, runs=None):
        self._runs = runs or []

    async def query(self, sql, parameters=None):
        if "status = 'error'" in sql:
            return self._runs
        return []


def _server(ch=None):
    ch = ch or _FakeCH()
    return build_mcp_server(get_ch=lambda: ch, get_pool=lambda: None)


@pytest.mark.asyncio
async def test_tools_registered():
    mcp = _server()
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"list_failed_runs", "get_run", "replay_run"}


@pytest.mark.asyncio
async def test_list_failed_runs_tool_executes():
    ch = _FakeCH(runs=[
        {"run_id": "r1", "name": "loop", "error_kind": "X",
         "error_message": "boom", "start_time": "2026-06-29T00:00:00Z"}
    ])
    mcp = _server(ch)
    _content, structured = await mcp.call_tool(
        "list_failed_runs", {"project_id": "p1", "limit": 5}
    )
    # FastMCP wraps a list return under {"result": [...]}
    rows = structured["result"] if isinstance(structured, dict) else structured
    assert rows[0]["run_id"] == "r1"
