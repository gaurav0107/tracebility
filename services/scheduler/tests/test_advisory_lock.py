"""Advisory lock mutual exclusion (real Postgres)."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from langprobe_scheduler.locks import advisory_lock

pytestmark = pytest.mark.asyncio


async def test_advisory_lock_is_mutually_exclusive(integration_dsn: str) -> None:
    # min_size=2 so the second acquire below gets a DIFFERENT session while
    # the first connection is still checked out — advisory xact locks are
    # per-session, so a same-session re-acquire would (correctly) also be True.
    pool = await asyncpg.create_pool(integration_dsn, min_size=2, max_size=4)
    try:
        key = f"test-lock:{uuid4()}"
        async with advisory_lock(pool, key) as (_conn1, got1):
            assert got1 is True
            async with advisory_lock(pool, key) as (_conn2, got2):
                assert got2 is False
        # first lock released on context exit -> re-acquirable
        async with advisory_lock(pool, key) as (_conn3, got3):
            assert got3 is True
    finally:
        await pool.close()
