"""server.py must not hard-fail on import when `mcp` isn't installed
(Task 8, D2-A) — the `mcp` package is an optional dependency here (the
`server` extra). Importing `langprobe_mcp_adapter.server` must always
succeed; only actually building a server should require `mcp`.

Note: `mcp` MAY be present in the environment (another workspace member,
`services/api`, depends on it). So the "mcp missing" test below forces
the absent state deterministically via `sys.modules`, rather than relying
on the ambient install state.
"""

from __future__ import annotations

import sys

import pytest


def test_importing_server_module_does_not_require_mcp():
    import langprobe_mcp_adapter.server as server_mod

    assert hasattr(server_mod, "build_server")


def test_build_server_without_mcp_raises_clear_install_hint(monkeypatch):
    import langprobe_mcp_adapter.server as server_mod

    # Force `import mcp` to fail regardless of whether the SDK is installed
    # in this env, so the guard boundary is tested deterministically.
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server", None)

    with pytest.raises(RuntimeError) as exc_info:
        server_mod.build_server("http://localhost:8000", "sess-cookie")

    assert "server" in str(exc_info.value)
    assert "langprobe-mcp-adapter" in str(exc_info.value)
