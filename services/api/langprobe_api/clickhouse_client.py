"""Thin ClickHouse client used by read-side routers.

clickhouse-connect's `Client` is sync AND not thread-safe — concurrent
calls within the same client raise "Attempt to execute concurrent
queries within the same session". One client per process can't serve
a fan-out request like the home page (which fires 3-4 reads in
parallel).

We keep a small thread-safe pool of clients. Each call checks one out
of the pool, runs the query in a thread, returns it. The pool grows
lazily up to a cap; no client is ever shared between two concurrent
calls.

Failure surfaces as an exception so routers can translate to 503; we
prefer that to a 200 with stale or empty data. The hot trace ingest
path stays on the dedicated ingest service — these helpers are for
low-volume CRUD + read fan-out only.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client


def _utc_aware(value: Any) -> Any:
    """Stamp UTC on naive datetimes coming out of ClickHouse.

    DateTime64 columns decode as naive datetimes; FastAPI then
    serializes them without a timezone suffix and every browser parses
    them as *local* time, shifting all timestamps by the viewer's UTC
    offset. ClickHouse stores UTC, so say so.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# Pool cap: deliberately conservative. Each client opens a small HTTP
# connection pool internally; we just need enough distinct sessions
# that the home-page fan-out (5-ish parallel reads) doesn't queue.
_DEFAULT_POOL_SIZE = 8


class ClickHouseQuery:
    def __init__(self, url: str, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        self._url = url
        self._pool_size = pool_size
        # `_pool` holds idle clients; `_created` tracks how many we've
        # made so we know when to grow vs. block on a checkout.
        self._pool: queue.Queue[Client] = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    def _checkout(self) -> Client:
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._closed:
                raise RuntimeError("ClickHouseQuery is closed")
            if self._created < self._pool_size:
                client = clickhouse_connect.get_client(dsn=self._url)
                self._created += 1
                return client
        # Pool is at cap; block until a client is returned.
        return self._pool.get()

    def _checkin(self, client: Client) -> None:
        if self._closed:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._pool.put(client)

    def _run(self, fn) -> Any:
        client = self._checkout()
        try:
            return fn(client)
        finally:
            self._checkin(client)

    async def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._run(lambda c: c.query(sql, parameters=parameters)),
        )
        column_names = result.column_names
        return [
            {k: _utc_aware(v) for k, v in zip(column_names, row, strict=True)}
            for row in result.result_rows
        ]

    async def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        column_names: Sequence[str],
    ) -> None:
        """Low-volume row insert for control-plane writes.

        Hot path traces still go through the ingest worker; this is for
        things like dataset_item where the API directly writes a handful
        of rows on user action.
        """
        if not rows:
            return
        loop = asyncio.get_running_loop()
        rows_list = list(rows)
        cols_list = list(column_names)
        await loop.run_in_executor(
            None,
            lambda: self._run(lambda c: c.insert(table, rows_list, column_names=cols_list)),
        )

    async def command(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        """Run a DDL/DML command (ALTER ... DELETE, etc.) without a result set."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._run(lambda c: c.command(sql, parameters=parameters)),
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True
        # Drain whatever is idle; in-flight clients get closed on
        # checkin via the closed-flag check.
        while True:
            try:
                client = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
