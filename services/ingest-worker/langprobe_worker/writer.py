"""ClickHouse bulk writer.

Each batch is one INSERT per table — ClickHouse loves big inserts, hates
small ones.

Idempotency: ``run`` and ``span`` are ReplacingMergeTree on
``(project_id, run_id, span_id)`` ordered by ``received_at``. So a redelivery
from the Redis stream produces a second row that the merge collapses. We
don't need to dedupe at write time.

Replay captures: for every span of kind tool/llm/retriever we derive a
``replay_capture`` row with a content-addressed sha256 over the bytes the
replayer would need (model+temperature+inputs+outputs+attributes for llm,
inputs+outputs for tool/retriever). Same byte payload → same hash → shared
``object_ref`` across runs. The ``object_ref`` is currently
``inline:sha256:<hash>`` because we keep the bytes in the span row itself;
when an object-store backend is wired we'll flip the ref to ``s3://...``
without touching the index.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import structlog
from clickhouse_connect.driver import Client

log = structlog.get_logger("langprobe.worker.writer")

_RUN_COLUMNS: tuple[str, ...] = (
    "org_id",
    "workspace_id",
    "project_id",
    "run_id",
    "parent_run_id",
    "name",
    "kind",
    "status",
    "sdk",
    "start_time",
    "end_time",
    "duration_ns",
    "received_at",
    "inputs",
    "outputs",
    "inputs_obj_ref",
    "outputs_obj_ref",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
    "session_id",
    "user_id",
    "tags",
    "metadata",
    "error_kind",
    "error_message",
    "schema_version",
)

_SPAN_COLUMNS: tuple[str, ...] = (
    "org_id",
    "workspace_id",
    "project_id",
    "run_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "status",
    "start_time",
    "end_time",
    "duration_ns",
    "received_at",
    "model",
    "temperature",
    "inputs",
    "outputs",
    "inputs_obj_ref",
    "outputs_obj_ref",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
    "attributes",
    "error_kind",
    "error_message",
    "schema_version",
)

_REPLAY_CAPTURE_COLUMNS: tuple[str, ...] = (
    "org_id",
    "workspace_id",
    "project_id",
    "run_id",
    "span_id",
    "kind",
    "content_hash",
    "object_ref",
    "size_bytes",
    "attributes",
    "captured_at",
    "schema_version",
)

# span.kind -> replay_capture.kind. Embedding/parser/chain/agent are
# orchestration concerns the replayer doesn't need to mock; only IO at
# the boundary matters for deterministic replay.
_REPLAY_KIND_BY_SPAN_KIND: dict[str, str] = {
    "llm": "llm_call",
    "tool": "tool_io",
    "retriever": "retrieval",
}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _epoch(value: Any) -> datetime:
    """ClickHouse columns are NOT NULL DateTime64; fall back to epoch."""
    parsed = _parse_dt(value)
    return parsed if parsed is not None else datetime.fromtimestamp(0, tz=UTC)


def _duration_ns(start: datetime | None, end: datetime | None) -> int | None:
    """Wall-clock latency in nanoseconds, or None when it can't be derived.

    The ClickHouse ``duration_ns`` column feeds the Traces latency column and
    every p50/p95/p99 percentile query. Native ingest carries start_time +
    end_time but no explicit duration, so the worker derives it. None when
    end_time is absent or precedes start_time (clock skew / bad input) rather
    than writing a bogus/negative latency.
    """
    if start is None or end is None:
        return None
    delta = end - start
    ns = int(delta.total_seconds() * 1_000_000_000)
    return ns if ns >= 0 else None


_MISSING_TENANT_UUID = "00000000-0000-0000-0000-000000000000"


def _tenant_tuple(envelope: dict[str, Any]) -> tuple[str, str, str]:
    """(org_id, workspace_id, project_id) from the envelope.

    During the rolling cutover (spec §9 step 6), envelopes from the
    pre-Phase-5 ingest-api lack ``org_id``/``workspace_id``. We can't drop
    those rows or the worker stalls; instead, write a sentinel UUID and
    flag the line so the operator sees a structured warning.

    Once Phase 5 is fully deployed, the warn count drops to zero. The
    sentinel rows can be reconciled out of ClickHouse via the
    ``project_tenant_dict`` lookup if anyone cares to backfill them.
    """
    org_id = envelope.get("org_id")
    workspace_id = envelope.get("workspace_id")
    project_id = envelope["project_id"]
    if not org_id or not workspace_id:
        log.warning(
            "envelope missing tenant fields; using sentinel",
            project_id=project_id,
            has_org=bool(org_id),
            has_workspace=bool(workspace_id),
        )
        return (
            org_id or _MISSING_TENANT_UUID,
            workspace_id or _MISSING_TENANT_UUID,
            project_id,
        )
    return (org_id, workspace_id, project_id)


def _row_for_run(envelope: dict[str, Any], run: dict[str, Any]) -> tuple[Any, ...]:
    received_at = _epoch(run.get("received_at") or envelope.get("received_at"))
    org_id, workspace_id, project_id = _tenant_tuple(envelope)
    start_time = _epoch(run.get("start_time"))
    end_time = _parse_dt(run.get("end_time"))
    return (
        org_id,
        workspace_id,
        project_id,
        run["run_id"],
        run.get("parent_run_id"),
        run.get("name") or "",
        run.get("kind") or "chain",
        run.get("status") or "ok",
        run.get("sdk") or envelope.get("source") or "",
        start_time,
        end_time,
        _duration_ns(start_time, end_time),
        received_at,
        run.get("inputs") or "",
        run.get("outputs") or "",
        run.get("inputs_obj_ref"),
        run.get("outputs_obj_ref"),
        run.get("prompt_tokens") or 0,
        run.get("completion_tokens") or 0,
        run.get("total_tokens") or 0,
        run.get("cost_usd") or 0,
        run.get("session_id"),
        run.get("user_id"),
        list(run.get("tags") or []),
        json.dumps(run.get("metadata") or {}),
        run.get("error_kind") or "",
        run.get("error_message") or "",
        run.get("schema_version") or 1,
    )


def _row_for_span(
    envelope: dict[str, Any],
    span: dict[str, Any],
    *,
    parent_run_id: str | None = None,
) -> tuple[Any, ...]:
    received_at = _epoch(envelope.get("received_at"))
    run_id = span.get("run_id") or parent_run_id
    org_id, workspace_id, project_id = _tenant_tuple(envelope)
    start_time = _epoch(span.get("start_time"))
    end_time = _parse_dt(span.get("end_time"))
    return (
        org_id,
        workspace_id,
        project_id,
        run_id,
        span["span_id"],
        span.get("parent_span_id"),
        span.get("name") or "",
        span.get("kind") or "chain",
        span.get("status") or "ok",
        start_time,
        end_time,
        _duration_ns(start_time, end_time),
        received_at,
        span.get("model") or "",
        span.get("temperature"),
        span.get("inputs") or "",
        span.get("outputs") or "",
        span.get("inputs_obj_ref"),
        span.get("outputs_obj_ref"),
        span.get("prompt_tokens") or 0,
        span.get("completion_tokens") or 0,
        span.get("total_tokens") or 0,
        span.get("cost_usd") or 0,
        json.dumps(span.get("attributes") or {}),
        span.get("error_kind") or "",
        span.get("error_message") or "",
        span.get("schema_version") or 1,
    )


def _content_hash(parts: list[str]) -> tuple[str, int]:
    """Stable sha256 hex over the concatenation of parts. Returns
    ``(hex_digest, total_bytes)``. ``parts`` are already strings (json
    or plain) so we can join them with a NUL separator that won't
    collide with the JSON itself."""
    blob = "\x00".join(parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    return digest, len(blob)


def _replay_payload_parts(span: dict[str, Any], replay_kind: str) -> list[str]:
    """Pick the byte payload that determines replay determinism per kind.

    LLM call: model + temperature + inputs + outputs. A different model
    string is the canonical replay-divergence signal (ER-18: warned, not
    silent-substituted).

    Tool I/O: inputs + outputs. The tool function itself is the contract;
    we capture its observed effect.

    Retrieval: inputs (the query) + outputs (the docs). If the index has
    drifted, the outputs differ.
    """
    inputs = span.get("inputs") or ""
    outputs = span.get("outputs") or ""
    if replay_kind == "llm_call":
        model = span.get("model") or ""
        temp = span.get("temperature")
        temp_str = "" if temp is None else f"{float(temp):.6f}"
        return [model, temp_str, inputs, outputs]
    return [inputs, outputs]


def _row_for_replay_capture(
    envelope: dict[str, Any],
    span: dict[str, Any],
    *,
    parent_run_id: str | None = None,
) -> tuple[Any, ...] | None:
    """Build a ``replay_capture`` row from a span. Returns None when the
    span is not replay-relevant (e.g. chain/agent orchestration)."""
    span_kind = span.get("kind") or ""
    replay_kind = _REPLAY_KIND_BY_SPAN_KIND.get(span_kind)
    if replay_kind is None:
        return None
    run_id = span.get("run_id") or parent_run_id
    if run_id is None:
        return None
    parts = _replay_payload_parts(span, replay_kind)
    digest, size_bytes = _content_hash(parts)
    captured_at = _epoch(envelope.get("received_at"))
    attrs = {
        "span_kind": span_kind,
        "name": span.get("name") or "",
        "model": span.get("model"),
        "temperature": span.get("temperature"),
    }
    org_id, workspace_id, project_id = _tenant_tuple(envelope)
    return (
        org_id,
        workspace_id,
        project_id,
        run_id,
        span["span_id"],
        replay_kind,
        digest,
        f"inline:sha256:{digest}",
        size_bytes,
        json.dumps({k: v for k, v in attrs.items() if v is not None}),
        captured_at,
        1,
    )


class ClickHouseWriter:
    def __init__(self, url: str) -> None:
        self._client: Client = clickhouse_connect.get_client(dsn=url)

    def insert_envelope(self, envelope: dict[str, Any]) -> tuple[int, int, int]:
        """Translate one envelope into row inserts.

        Returns ``(runs, spans, replay_captures)`` counts. The third
        element was added in v0.2 of the worker; old log lines that
        unpacked ``(runs, spans)`` still work because tuple-of-three is
        also iterable, but consumers reading by index should pick up the
        new field.
        """
        payload = envelope.get("payload") or {}
        runs = payload.get("runs") or []
        loose_spans = payload.get("spans") or []

        run_rows: list[tuple[Any, ...]] = []
        span_rows: list[tuple[Any, ...]] = []
        capture_rows: list[tuple[Any, ...]] = []
        for r in runs:
            run_rows.append(_row_for_run(envelope, r))
            for s in r.get("spans") or []:
                span_rows.append(_row_for_span(envelope, s, parent_run_id=r["run_id"]))
                cap = _row_for_replay_capture(envelope, s, parent_run_id=r["run_id"])
                if cap is not None:
                    capture_rows.append(cap)
        for s in loose_spans:
            span_rows.append(_row_for_span(envelope, s))
            cap = _row_for_replay_capture(envelope, s)
            if cap is not None:
                capture_rows.append(cap)

        if run_rows:
            self._client.insert("run", run_rows, column_names=_RUN_COLUMNS)
        if span_rows:
            self._client.insert("span", span_rows, column_names=_SPAN_COLUMNS)
        if capture_rows:
            try:
                self._client.insert(
                    "replay_capture",
                    capture_rows,
                    column_names=_REPLAY_CAPTURE_COLUMNS,
                )
            except Exception as exc:  # noqa: BLE001
                # Replay index is a derived store. Don't drop the trace
                # because the capture insert tripped — log and move on.
                # Per ER-23 we never silent-drop primary trace data, but
                # the capture index can be backfilled later from spans.
                log.warning(
                    "replay_capture insert failed",
                    error=str(exc),
                    captures=len(capture_rows),
                )
        return (len(run_rows), len(span_rows), len(capture_rows))

    def close(self) -> None:
        self._client.close()
