"""Typed I/O for the agent-native eval-loop verbs (Task 2, D2-A).

These are the provisional request/response shapes for the 5 versioned
verbs (``langprobe.v1.*``). They are consumed by the (stubbed) service
functions in ``verbs/service.py`` and, later, by the HTTP router and
the MCP adapter — both dispatch through the same models so request
validation never drifts between the two surfaces.

Pydantic v2 style throughout (``BaseModel`` + type-hint fields, no v1
``Config`` classes).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from langprobe_api.verbs.lifecycle import BacktestStatus, DraftStatus

# --- cluster_failures -----------------------------------------------------


class ClusterFailuresIn(BaseModel):
    project_id: UUID
    window_hours: int
    group_by: Literal["error", "tool", "status"] = "error"


class Cluster(BaseModel):
    key: str
    count: int
    sample_run_ids: list[UUID]


class ClusterFailuresOut(BaseModel):
    clusters: list[Cluster]


# --- propose_eval ----------------------------------------------------------


class ProposeEvalIn(BaseModel):
    # Clusters are NOT persisted (cluster_failures is a read-only triage
    # view over ClickHouse) — there is no `cluster_id` to look back up.
    # The caller instead passes back the sample_run_ids + group_key it
    # got from a `Cluster` in `ClusterFailuresOut`, making this verb
    # stateless.
    project_id: UUID
    sample_run_ids: list[UUID] = Field(min_length=1, max_length=20)
    group_key: str


class EvalDraftOut(BaseModel):
    draft_id: UUID
    judge_kind: str
    judge_config: dict
    status: DraftStatus


# --- run_judge_over_cohort ---------------------------------------------


class BacktestIn(BaseModel):
    draft_id: UUID
    window_hours: int


class BacktestOut(BaseModel):
    backtest_run_id: UUID
    status: BacktestStatus


# --- promote_to_recurring ------------------------------------------------


class PromoteIn(BaseModel):
    draft_id: UUID
    approval_token: str


class PromoteOut(BaseModel):
    judge_id: UUID


# --- watch_judge -----------------------------------------------------------


class WatchIn(BaseModel):
    target_id: UUID


class WatchOut(BaseModel):
    status: str
    caught: int | None
    missed: int | None
    error: str | None


# --- HTTP route request models (Task 7) -------------------------------------
#
# `BacktestIn`/`PromoteIn`/`WatchIn` above carry no `project_id` (the verb
# resolves it indirectly, via the draft/run row they reference). The HTTP
# router still needs a `project_id` up front to scope-check the caller
# *before* touching any data, so these route-only request models bundle it
# alongside the verb's own params. `cluster_failures`/`propose_eval` need no
# equivalent — `ClusterFailuresIn`/`ProposeEvalIn` already carry `project_id`.


class BacktestRequest(BaseModel):
    project_id: UUID
    draft_id: UUID
    window_hours: int

    def to_verb_params(self) -> BacktestIn:
        return BacktestIn(draft_id=self.draft_id, window_hours=self.window_hours)


class PromoteRequest(BaseModel):
    project_id: UUID
    draft_id: UUID
    approval_token: str

    def to_verb_params(self) -> PromoteIn:
        return PromoteIn(draft_id=self.draft_id, approval_token=self.approval_token)


class WatchRequest(BaseModel):
    project_id: UUID
    target_id: UUID

    def to_verb_params(self) -> WatchIn:
        return WatchIn(target_id=self.target_id)
