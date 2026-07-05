"""Verb service layer (Task 2, D2-A; Tasks 3-6 fill in the 5 verbs).

Each function below is the single source of truth a future HTTP router
and the MCP adapter will both call — neither surface should re-implement
this logic. ``cluster_failures`` delegates to ``verbs/cluster.py``;
``propose_eval`` to ``verbs/propose.py``; ``run_judge_over_cohort`` to
``verbs/backtest.py``; ``promote_to_recurring`` to ``verbs/promote.py``;
``watch_judge`` to ``verbs/watch.py``. All 5 verbs are real as of Task 6.

Every verb takes a ``VerbDeps`` bundle (Postgres pool + ClickHouse
client) and the caller's ``TenantContext`` first, so implementers can
call :func:`langprobe_api.verbs.scope.require_project_scope` before
touching any data.
"""

from __future__ import annotations

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs import backtest, cluster, promote, propose, watch
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
    return await propose.propose_eval(deps, ctx, params)


async def run_judge_over_cohort(
    deps: VerbDeps, ctx: TenantContext, params: BacktestIn
) -> BacktestOut:
    return await backtest.run_judge_over_cohort(deps, ctx, params)


async def promote_to_recurring(deps: VerbDeps, ctx: TenantContext, params: PromoteIn) -> PromoteOut:
    return await promote.promote_to_recurring(deps, ctx, params)


async def watch_judge(deps: VerbDeps, ctx: TenantContext, params: WatchIn) -> WatchOut:
    return await watch.watch_judge(deps, ctx, params)
