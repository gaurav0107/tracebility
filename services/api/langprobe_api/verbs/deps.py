"""Dependency bundle for the verb service layer (Task 3, D2-A).

The verb skeleton (Task 2) took only ``(ctx, params)`` — no way to
reach Postgres or ClickHouse. ``VerbDeps`` is the missing piece: a
single frozen bundle each verb takes so it can query either store
without importing app-global state directly. The HTTP router and MCP
adapter are both expected to construct one ``VerbDeps`` per request
from ``request.app.state`` (mirroring how ``routers/threads_query.py``
reaches ``request.app.state.pg`` / ``.clickhouse``) and pass it through
to ``verbs/registry.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from langprobe_api.clickhouse_client import ClickHouseQuery


@dataclass(frozen=True)
class VerbDeps:
    pool: asyncpg.Pool
    ch: ClickHouseQuery
