"""Pydantic ingest schemas.

Field names align with OpenTelemetry GenAI semantic conventions where possible
so OTel-native SDKs can submit without translation. The LangSmith shim translates
LangSmith's ``RunCreate`` / ``RunUpdate`` payloads to these models in the router.

We accept extra fields (``extra='allow'``) so SDK additions don't break ingest;
unknown fields land in the run/span ``metadata`` jsonStr column downstream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RunKind = Literal[
    "llm",
    "chain",
    "tool",
    "agent",
    "retriever",
    "embedding",
    "parser",
    "workflow",
    "task",
    "guardrail",
    "evaluator",
    "reranker",
]
RunStatus = Literal["ok", "error", "running", "cancelled"]


class SpanIngest(BaseModel):
    """One span. Mirrors clickhouse.span columns 1:1; the worker writes them."""

    model_config = ConfigDict(extra="allow")

    span_id: UUID
    run_id: UUID
    parent_span_id: UUID | None = None
    name: str
    kind: RunKind
    status: RunStatus = "ok"
    start_time: datetime
    end_time: datetime | None = None
    model: str | None = None
    temperature: float | None = None
    inputs: str | None = None
    outputs: str | None = None
    inputs_obj_ref: str | None = None
    outputs_obj_ref: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    error_kind: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class RunIngest(BaseModel):
    """One run (root or child). Mirrors clickhouse.run columns."""

    model_config = ConfigDict(extra="allow")

    run_id: UUID
    parent_run_id: UUID | None = None
    name: str
    kind: RunKind
    status: RunStatus = "ok"
    sdk: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    received_at: datetime | None = None
    inputs: str | None = None
    outputs: str | None = None
    inputs_obj_ref: str | None = None
    outputs_obj_ref: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    user_id: str | None = None
    end_user_id: str | None = None
    end_user_metadata: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_kind: str | None = None
    error_message: str | None = None
    spans: list[SpanIngest] = Field(default_factory=list)


class IngestBatch(BaseModel):
    """Top-level ingest envelope. SDKs flush these on a timer/size threshold."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    sdk: str | None = None
    sdk_version: str | None = None
    runs: list[RunIngest] = Field(default_factory=list)
    spans: list[SpanIngest] = Field(default_factory=list)


class IngestAck(BaseModel):
    """202 response body. We don't surface internal queue identifiers."""

    accepted_runs: int
    accepted_spans: int
