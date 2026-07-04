"""Stubbed verb service layer (Task 2, D2-A).

Each function below is the single source of truth a future HTTP router
and the MCP adapter will both call — neither surface should re-implement
this logic. All 5 are stubbed to raise ``NotImplementedError`` here;
later tasks fill in the real behavior (ClickHouse cluster queries, the
judge backtest runner, promotion + audit-log writes, and the drift
watcher).

Every verb takes the caller's ``TenantContext`` first, so implementers
can call :func:`langprobe_api.verbs.scope.require_project_scope` before
touching any data.
"""

from __future__ import annotations

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs.models import (
    BacktestIn,
    BacktestOut,
    ClusterFailuresIn,
    ClusterFailuresOut,
    EvalDraftOut,
    PromoteIn,
    PromoteOut,
    ProposeEvalIn,
    WatchIn,
    WatchOut,
)


async def cluster_failures(ctx: TenantContext, params: ClusterFailuresIn) -> ClusterFailuresOut:
    raise NotImplementedError("langprobe.v1.cluster_failures — implemented in a later task")


async def propose_eval(ctx: TenantContext, params: ProposeEvalIn) -> EvalDraftOut:
    raise NotImplementedError("langprobe.v1.propose_eval — implemented in a later task")


async def run_judge_over_cohort(ctx: TenantContext, params: BacktestIn) -> BacktestOut:
    raise NotImplementedError("langprobe.v1.run_judge_over_cohort — implemented in a later task")


async def promote_to_recurring(ctx: TenantContext, params: PromoteIn) -> PromoteOut:
    raise NotImplementedError("langprobe.v1.promote_to_recurring — implemented in a later task")


async def watch_judge(ctx: TenantContext, params: WatchIn) -> WatchOut:
    raise NotImplementedError("langprobe.v1.watch_judge — implemented in a later task")
