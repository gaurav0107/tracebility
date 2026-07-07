"""reaper_loop calls reap on its interval and shuts down on cancel."""

from __future__ import annotations

import asyncio

import pytest
from langprobe_scheduler.app import reaper_loop, recurring_loop

pytestmark = pytest.mark.asyncio


async def test_reaper_loop_invokes_reap_then_cancels() -> None:
    seen: list[int] = []

    async def fake_reap(pool, *, lease_timeout_s: int) -> int:
        seen.append(lease_timeout_s)
        return 0

    task = asyncio.create_task(reaper_loop(None, interval_s=0, lease_timeout_s=99, _reap=fake_reap))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen  # ran at least once
    assert seen[0] == 99  # lease_timeout_s threaded through


async def test_reaper_loop_survives_a_failing_tick() -> None:
    calls = {"n": 0}

    async def flaky_reap(pool, *, lease_timeout_s: int) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return 0

    task = asyncio.create_task(reaper_loop(None, interval_s=0, lease_timeout_s=1, _reap=flaky_reap))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2  # kept going after the exception


async def test_recurring_loop_invokes_eval_with_max_cohort() -> None:
    seen: list[int] = []

    async def fake_eval(pool, clickhouse, *, max_cohort: int) -> int:
        seen.append(max_cohort)
        return 0

    task = asyncio.create_task(
        recurring_loop(None, None, interval_s=0, max_cohort=42, _eval=fake_eval)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen  # ran at least once
    assert seen[0] == 42  # max_cohort threaded through
