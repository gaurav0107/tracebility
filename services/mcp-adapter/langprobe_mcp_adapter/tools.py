"""Tool schemas for the 4 agent-drivable eval-loop verbs (Task 8, D2-A).

These mirror the verb service layer's Pydantic request models
(``langprobe_api.verbs.models``) closely enough for an external agent
to construct a valid call, without this package depending on the api
package at all — the adapter is a standalone workspace member that
only talks to the api over HTTP (see ``client.py``).

``promote_to_recurring`` is DELIBERATELY EXCLUDED from this registry.
Promoting a draft mutates production judge config and is the
human-approval choke point in the design (see
``services/api/langprobe_api/routers/verbs.py``'s ``post_promote``,
which is gated behind a session principal on purpose). An MCP tool
call is inherently agent-driven, so exposing ``promote`` here would
punch a hole through that security boundary — it must only ever be
reachable via the session-gated HTTP route, driven by a human.

No ``mcp`` import in this module — the tool shapes below are plain
dicts so tests (and any future non-MCP caller) can use them without
the optional ``mcp`` SDK installed.
"""

from __future__ import annotations

CLUSTER_FAILURES_TOOL = {
    "name": "cluster_failures",
    "description": (
        "Group a project's recent failing runs by error kind, tool name, "
        "or status, so an agent can see which failure mode dominates "
        "before drafting an eval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "format": "uuid"},
            "window_hours": {"type": "integer", "minimum": 1},
            "group_by": {
                "type": "string",
                "enum": ["error", "tool", "status"],
                "default": "error",
            },
        },
        "required": ["project_id", "window_hours"],
    },
}

PROPOSE_EVAL_TOOL = {
    "name": "propose_eval",
    "description": (
        "Draft a prompted-judge rubric from a failure cluster's sample "
        "run ids, via an LLM proposer. Returns a backtest_draft ready to "
        "be backtested."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "format": "uuid"},
            "sample_run_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 1,
                "maxItems": 20,
            },
            "group_key": {"type": "string"},
        },
        "required": ["project_id", "sample_run_ids", "group_key"],
    },
}

RUN_JUDGE_OVER_COHORT_TOOL = {
    "name": "run_judge_over_cohort",
    "description": (
        "Run a backtest_draft's judge over a bounded, most-recent slice "
        "of the project's failing history, to see whether it would have "
        "caught those failures. Returns a queued backtest_run id; poll "
        "watch_judge for status."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "format": "uuid"},
            "draft_id": {"type": "string", "format": "uuid"},
            "window_hours": {"type": "integer", "minimum": 0},
        },
        "required": ["project_id", "draft_id", "window_hours"],
    },
}

WATCH_JUDGE_TOOL = {
    "name": "watch_judge",
    "description": (
        "Poll a backtest_run's status. Returns caught/missed counts once "
        "done, or an error if the run failed (including a stale-heartbeat "
        "timeout)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "format": "uuid"},
            "target_id": {"type": "string", "format": "uuid"},
        },
        "required": ["project_id", "target_id"],
    },
}

# The complete set of agent-drivable tools this adapter exposes.
# `promote_to_recurring` is intentionally absent — see module docstring.
TOOLS = [
    CLUSTER_FAILURES_TOOL,
    PROPOSE_EVAL_TOOL,
    RUN_JUDGE_OVER_COHORT_TOOL,
    WATCH_JUDGE_TOOL,
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}
