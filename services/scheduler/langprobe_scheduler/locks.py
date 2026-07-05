"""Postgres advisory locks for multi-replica safety.

Every unit of periodic work takes a transaction-scoped advisory lock
keyed on a stable string before touching its row(s). Two scheduler
replicas can then run the same tick concurrently and still never act on
the same work item: whoever loses the race sees ``got=False`` and skips.
The lock auto-releases when the wrapping transaction ends (commit or
connection death), so a crashed replica never wedges a work item.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg


@asynccontextmanager
async def advisory_lock(
    pool: asyncpg.Pool, key: str
) -> AsyncIterator[tuple[asyncpg.Connection, bool]]:
    """Try to take a transaction-scoped advisory lock named ``key``.

    Yields ``(conn, got)``. Do the guarded work on ``conn`` only when
    ``got`` is True; the lock is held until this context exits.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            got = await conn.fetchval(
                "select pg_try_advisory_xact_lock(hashtextextended($1, 0))", key
            )
            yield conn, bool(got)
