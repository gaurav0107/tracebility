"""langprobe-mcp-adapter: thin MCP translation layer over the agent-
drivable eval-loop verbs (Task 8, D2-A).

See ``tools.py`` for the exposed tool registry and ``client.py`` for
the HTTP translation layer. ``server.py`` is the actual MCP entrypoint
and requires the optional ``mcp`` package (``pip install
langprobe-mcp-adapter[server]``); everything else in this package is
importable without it.
"""

from __future__ import annotations
