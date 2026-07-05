"""MCP server entrypoint for the eval-loop verb adapter (Task 8, D2-A).

This is the ONLY module in the package that imports the optional `mcp`
SDK, and it does so lazily (inside a function) so that importing
`langprobe_mcp_adapter` — or any other module in this package — never
hard-fails when `mcp` isn't installed. Install it via the `server`
extra: `pip install langprobe-mcp-adapter[server]`.

Thin by design: this module registers the 4 tools from `tools.py` and
routes each MCP tool call to the matching `VerbHTTPClient` method from
`client.py`. It holds no business logic of its own.

Not imported by any test in this package — tests exercise `tools.py`
and `client.py` directly, which are free of any `mcp` dependency.
"""

from __future__ import annotations

from typing import Any

from langprobe_mcp_adapter.client import VerbCallError, VerbHTTPClient
from langprobe_mcp_adapter.tools import TOOLS

_INSTALL_HINT = (
    "the 'mcp' package is not installed. Install the 'server' extra to run "
    "the MCP server: pip install langprobe-mcp-adapter[server]"
)

# Maps each exposed tool name to the VerbHTTPClient method that serves
# it. Kept as a plain dict (not a decorator registry) so it stays
# trivially inspectable/testable without importing `mcp`.
_VERB_METHODS: dict[str, str] = {
    "cluster_failures": "cluster_failures",
    "propose_eval": "propose_eval",
    "run_judge_over_cohort": "run_judge_over_cohort",
    "watch_judge": "watch_judge",
}


def _require_mcp() -> Any:
    try:
        import mcp  # noqa: F401
        import mcp.server
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return mcp.server


async def _dispatch_tool_call(client: VerbHTTPClient, name: str, arguments: dict) -> dict:
    """Route one MCP tool call to the matching VerbHTTPClient method.

    Raises :class:`VerbCallError` unchanged on a non-2xx api response —
    the MCP server wrapper (not this function) is responsible for
    translating that into whatever error shape MCP expects.
    """
    if name not in _VERB_METHODS:
        raise ValueError(f"unknown tool: {name!r}")
    method = getattr(client, _VERB_METHODS[name])
    try:
        return await method(arguments)
    except VerbCallError:
        raise


def build_server(base_url: str, session_cookie: str) -> Any:
    """Construct an MCP server instance wired to the 4 agent-drivable
    verb tools, dispatching each call over HTTP to `base_url` via a
    `VerbHTTPClient` carrying `session_cookie`.

    Raises `RuntimeError` with an install hint if the optional `mcp`
    package is not installed. This function — not module import — is
    the hard-fail boundary for the missing dependency.
    """
    server_module = _require_mcp()
    client = VerbHTTPClient(base_url=base_url, session_cookie=session_cookie)

    server = server_module.Server("langprobe-mcp-adapter")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        return await _dispatch_tool_call(client, name, arguments)

    return server


def main() -> None:
    import os

    base_url = os.environ.get("LANGPROBE_API_BASE_URL", "http://localhost:8000")
    session_cookie = os.environ.get("LANGPROBE_SESSION_COOKIE", "")
    if not session_cookie:
        raise RuntimeError(
            "LANGPROBE_SESSION_COOKIE must be set to a valid langprobe_session "
            "cookie value before starting the MCP server"
        )
    build_server(base_url, session_cookie)


if __name__ == "__main__":
    main()
