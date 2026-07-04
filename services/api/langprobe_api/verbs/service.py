"""Verb service layer (Task 2, D2-A; Task 3 fills in ``cluster_failures``).

Each function below is the single source of truth a future HTTP router
and the MCP adapter will both call — neither surface should re-implement
this logic. ``cluster_failures`` delegates to ``verbs/cluster.py``;
``run_judge_over_cohort`` delegates to ``verbs/backtest.py``. The
remaining 2 stay stubbed to raise ``NotImplementedError`` here — later
tasks fill in the real behavior (promotion + audit-log writes, and the
drift watcher).

Every verb takes a ``VerbDeps`` bundle (Postgres pool + ClickHouse
client) and the caller's ``TenantContext`` first, so implementers can
call :func:`langprobe_api.verbs.scope.require_project_scope` before
touching any data.
"""

from __future__ import annotations

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs import backtest, cluster
from langprobe_api.verbs.deps import VerbDeps
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


async def cluster_failures(
    deps: VerbDeps, ctx: TenantContext, params: ClusterFailuresIn
) -> ClusterFailuresOut:
    return await cluster.cluster_failures(deps, ctx, params)


async def propose_eval(deps: VerbDeps, ctx: TenantContext, params: ProposeEvalIn) -> EvalDraftOut:
    raise NotImplementedError("langprobe.v1.propose_eval — implemented in a later task")


async def run_judge_over_cohort(
    deps: VerbDeps, ctx: TenantContext, params: BacktestIn
) -> BacktestOut:
    return await backtest.run_judge_over_cohort(deps, ctx, params)


async def promote_to_recurring(deps: VerbDeps, ctx: TenantContext, params: PromoteIn) -> PromoteOut:
    raise NotImplementedError("langprobe.v1.promote_to_recurring — implemented in a later task")


async def watch_judge(deps: VerbDeps, ctx: TenantContext, params: WatchIn) -> WatchOut:
    raise NotImplementedError("langprobe.v1.watch_judge — implemented in a later task")
