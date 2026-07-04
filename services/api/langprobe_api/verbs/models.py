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

from pydantic import BaseModel

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
    cluster_id: UUID


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
