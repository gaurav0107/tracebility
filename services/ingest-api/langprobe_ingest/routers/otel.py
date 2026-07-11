"""OTLP / OpenInference ingest shim.

Accepts OTLP HTTP/JSON span payloads at the standard collector path
``POST /v1/traces`` and translates them into the native ``IngestBatch``
envelope used by every other ingest path. The worker is path-agnostic:
once enqueued, spans land in ClickHouse the same way native or
LangSmith-shim runs do.

Wedge:
- An OTel-instrumented agent (LlamaIndex, OpenAI Agents SDK, custom)
  already has an OTLP exporter. Point ``OTEL_EXPORTER_OTLP_ENDPOINT``
  at this host and traces start flowing without ripping out the SDK.
- OpenInference semantic conventions are a superset of OTel GenAI;
  we read both. Span ``openinference.span.kind`` (LLM / TOOL /
  CHAIN / RETRIEVER / EMBEDDING / AGENT / RERANKER) is the primary
  kind signal, with a fallback to OTel's ``gen_ai.operation.name``
  and finally the span name heuristic.

Trace/span id mapping:
- OTel ``traceId`` is 32-hex (128 bits) — maps directly to UUID.
- OTel ``spanId`` is 16-hex (64 bits) — left-padded with zeros to
  32 hex chars, then parsed as UUID. The high 64 bits are stable
  zero so two different runs with the same span_id collide; but
  inside a trace, span_id is unique, and we always pair with run_id
  on every write, so the collision is harmless.
- We synthesize a top-level ``Run`` per trace: name + kind from the
  trace's root span (lowest depth in the batch); start/end times
  bracket all spans. If the worker later sees more spans for the
  same trace they extend the run record (worker is idempotent on
  run_id).

Determinism note (ER-23): never silent-drop. If a span lacks a
required field (trace_id / span_id / name), we skip THAT span and
log; we don't tank the batch. The 202 ack reports accepted counts
honestly.

Format note: this endpoint accepts BOTH OTLP HTTP/JSON and OTLP
HTTP/protobuf. A stock ``opentelemetry-exporter-otlp-proto-http``
exporter sends ``application/x-protobuf``; the older/manual JSON
setups send ``application/json``. We dispatch on Content-Type and
translate both into the identical native ``IngestBatch`` — the rest
of the pipeline (redaction, enqueue, quota, 202 ack) is format-blind.

Protobuf id encoding: proto ``trace_id`` / ``span_id`` / ``parent_span_id``
are ``bytes`` fields. ``MessageToDict`` base64-encodes them, but the
translator expects hex (OTLP-JSON convention). We rewrite those three
fields to hex in a tiny normalization pass before translation so
``_trace_id_to_uuid`` / ``_span_id_to_uuid`` stay format-agnostic.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from uuid import UUID

import orjson
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from langprobe_tenant import QuotaMeter, TenantContext
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from ..enqueue import IngestEnqueue, serialize_batch
from ..limits import INGEST_GATING_METER, enforce_quota
from ..redactor import Redactor
from ..schemas import IngestAck, IngestBatch, RunIngest, RunKind, SpanIngest

log = structlog.get_logger("langprobe.ingest.otel")

router = APIRouter(tags=["otel-shim"])


# ---------------------------------------------------------------------------
# Attribute mapping — versioned DATA, not code.
#
# The kind maps and the ordered fallback key-lists that classify incoming
# OTel/OpenInference/OpenLLMetry spans into our canonical schema live in a
# packaged JSON file (``attribute_mapping.json``, sibling of the package's
# ``__init__``). It is loaded ONCE at import time into the module constants
# below. Adding a new source convention (a new vendor's token/model key, a new
# OpenInference kind alias) needs ZERO python changes — only a JSON edit.
# ---------------------------------------------------------------------------


def _load_attribute_mapping() -> dict[str, Any]:
    # attribute_mapping.json lives at the package root (langprobe_ingest/),
    # one level up from this routers/ subpackage.
    raw = files("langprobe_ingest").joinpath("attribute_mapping.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data.get("version"), int):
        raise RuntimeError("attribute_mapping.json must have an integer 'version'")
    return data


_ATTRIBUTE_MAPPING: dict[str, Any] = _load_attribute_mapping()

ATTRIBUTE_MAPPING_VERSION: int = _ATTRIBUTE_MAPPING["version"]

_OPENINFERENCE_KIND: dict[str, RunKind] = dict(_ATTRIBUTE_MAPPING["openinference_kind"])
_GEN_AI_OPERATION_KIND: dict[str, RunKind] = dict(_ATTRIBUTE_MAPPING["gen_ai_operation_kind"])
_MODEL_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["model"])
_TEMPERATURE_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["temperature"])
_PROMPT_TOKEN_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["tokens"]["prompt"])
_COMPLETION_TOKEN_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["tokens"]["completion"])
_TOTAL_TOKEN_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["tokens"]["total"])
_INPUT_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["io"]["input"])
_OUTPUT_KEYS: list[str] = list(_ATTRIBUTE_MAPPING["io"]["output"])
# End-user identity (the human the agent serves), distinct from the operator.
# Only stamped on the synthesized Run from the trace's root span attributes.
_END_USER_ID_KEYS: list[str] = list(_ATTRIBUTE_MAPPING.get("end_user_id") or [])
# Session / thread grouping key. OpenInference emits ``session.id``; OTel GenAI
# uses ``gen_ai.conversation.id``. Without this the run's session_id stays NULL
# and the trace never rolls up under /runs?view=threads.
_SESSION_ID_KEYS: list[str] = list(_ATTRIBUTE_MAPPING.get("session_id") or [])

_STATUS_OK = 1
_STATUS_ERROR = 2


# ---------------------------------------------------------------------------
# Attribute decoding (OTLP JSON: {"key": "...", "value": {"stringValue": "..."}})
# ---------------------------------------------------------------------------


def _decode_any_value(av: Any) -> Any:
    if not isinstance(av, dict):
        return av
    if "stringValue" in av:
        return av["stringValue"]
    if "intValue" in av:
        try:
            return int(av["intValue"])
        except (TypeError, ValueError):
            return av["intValue"]
    if "doubleValue" in av:
        try:
            return float(av["doubleValue"])
        except (TypeError, ValueError):
            return av["doubleValue"]
    if "boolValue" in av:
        return bool(av["boolValue"])
    if "arrayValue" in av:
        values = av["arrayValue"].get("values") or []
        return [_decode_any_value(v) for v in values]
    if "kvlistValue" in av:
        return _decode_attributes(av["kvlistValue"].get("values") or [])
    return None


def _decode_attributes(raw: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        out[key] = _decode_any_value(entry.get("value"))
    return out


# ---------------------------------------------------------------------------
# ID translation
# ---------------------------------------------------------------------------


def _trace_id_to_uuid(raw: Any) -> UUID | None:
    if not isinstance(raw, str) or not raw:
        return None
    hex_str = raw.lower().replace("-", "")
    if len(hex_str) != 32:
        return None
    try:
        return UUID(hex_str)
    except (TypeError, ValueError):
        return None


def _span_id_to_uuid(raw: Any) -> UUID | None:
    if not isinstance(raw, str) or not raw:
        return None
    hex_str = raw.lower().replace("-", "")
    # OTel span_id is 16 hex (64 bits); UUID needs 32. Left-pad
    # with zeros to keep mapping reversible.
    if len(hex_str) == 16:
        hex_str = "0" * 16 + hex_str
    if len(hex_str) != 32:
        return None
    try:
        return UUID(hex_str)
    except (TypeError, ValueError):
        return None


def _unix_nano_to_dt(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        ns = int(raw)
    except (TypeError, ValueError):
        return None
    if ns <= 0:
        return None
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)


# ---------------------------------------------------------------------------
# Kind / model / token extraction
# ---------------------------------------------------------------------------


def _resolve_kind(attrs: dict[str, Any], name: str) -> RunKind:
    raw_kind = attrs.get("openinference.span.kind")
    if isinstance(raw_kind, str):
        mapped = _OPENINFERENCE_KIND.get(raw_kind.upper())
        if mapped:
            return mapped
    op = attrs.get("gen_ai.operation.name")
    if isinstance(op, str):
        mapped = _GEN_AI_OPERATION_KIND.get(op.lower())
        if mapped:
            return mapped
    lower = name.lower()
    if "embed" in lower:
        return "embedding"
    if "retriev" in lower or "search" in lower:
        return "retriever"
    if lower.startswith("tool.") or lower.startswith("function."):
        return "tool"
    if lower.startswith("agent."):
        return "agent"
    if lower.startswith("chat.") or lower.startswith("llm."):
        return "llm"
    return "chain"


def _first_str(attrs: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        v = attrs.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _first_int(attrs: dict[str, Any], keys: list[str]) -> int | None:
    for key in keys:
        parsed = _int(attrs.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_model(attrs: dict[str, Any]) -> str | None:
    return _first_str(attrs, _MODEL_KEYS)


def _resolve_temperature(attrs: dict[str, Any]) -> float | None:
    for key in _TEMPERATURE_KEYS:
        v = attrs.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _resolve_tokens(attrs: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    prompt = _first_int(attrs, _PROMPT_TOKEN_KEYS)
    completion = _first_int(attrs, _COMPLETION_TOKEN_KEYS)
    total = _first_int(attrs, _TOTAL_TOKEN_KEYS)
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    return prompt, completion, total


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return orjson.dumps(value).decode("utf-8")


def _first_truthy(attrs: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        v = attrs.get(key)
        if v:
            return v
    return None


def _resolve_io(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    inputs: Any = _first_truthy(attrs, _INPUT_KEYS)
    outputs: Any = _first_truthy(attrs, _OUTPUT_KEYS)
    return (
        _stringify(inputs) if inputs is not None else None,
        _stringify(outputs) if outputs is not None else None,
    )


def _resolve_status(span: dict[str, Any]) -> tuple[str, str | None, str | None]:
    raw = span.get("status") or {}
    code = raw.get("code")
    message = raw.get("message")
    if isinstance(code, str):
        # JSON wire uses "OK"/"ERROR"; protobuf MessageToDict emits the proto
        # enum name "STATUS_CODE_OK"/"STATUS_CODE_ERROR". Accept both.
        normalized = code.upper().removeprefix("STATUS_CODE_")
        code_n = {"OK": _STATUS_OK, "ERROR": _STATUS_ERROR}.get(normalized, 0)
    else:
        try:
            code_n = int(code or 0)
        except (TypeError, ValueError):
            code_n = 0
    if code_n == _STATUS_ERROR:
        return "error", None, message
    return "ok", None, None


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _translate_spans(
    payload: dict[str, Any],
) -> tuple[list[RunIngest], int]:
    """Walk OTLP resourceSpans → group by trace → emit Run + Spans.

    Returns (runs, skipped_count). Skipped spans either had missing
    trace/span ids or failed validation; we log skips and continue
    rather than tank the batch (ER-23).
    """
    resource_spans = payload.get("resourceSpans") or []
    skipped = 0

    # Bucket spans by trace_id first so we can synthesize one Run per
    # trace with bracketed start/end times.
    buckets: dict[UUID, dict[str, Any]] = {}

    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        resource_attrs = _decode_attributes((rs.get("resource") or {}).get("attributes") or [])
        for ss in rs.get("scopeSpans") or []:
            if not isinstance(ss, dict):
                continue
            scope_name = (ss.get("scope") or {}).get("name") or ""
            for raw_span in ss.get("spans") or []:
                if not isinstance(raw_span, dict):
                    skipped += 1
                    continue
                trace_id = _trace_id_to_uuid(raw_span.get("traceId"))
                span_id = _span_id_to_uuid(raw_span.get("spanId"))
                if trace_id is None or span_id is None:
                    skipped += 1
                    log.debug(
                        "otel span skipped: missing/invalid ids",
                        trace_id=raw_span.get("traceId"),
                        span_id=raw_span.get("spanId"),
                    )
                    continue
                name = raw_span.get("name") or "span"
                attrs = _decode_attributes(raw_span.get("attributes") or [])
                kind = _resolve_kind(attrs, name)
                start = _unix_nano_to_dt(raw_span.get("startTimeUnixNano"))
                end = _unix_nano_to_dt(raw_span.get("endTimeUnixNano"))
                if start is None:
                    start = datetime.now(UTC)
                status_str, error_kind, error_message = _resolve_status(raw_span)
                inputs, outputs = _resolve_io(attrs)
                prompt_tok, comp_tok, total_tok = _resolve_tokens(attrs)
                parent_uuid = _span_id_to_uuid(raw_span.get("parentSpanId"))

                # Merge resource attrs in for the worker (debugging,
                # filtering) but namespace them so they can't collide.
                merged_attrs: dict[str, Any] = {"otel.scope.name": scope_name} if scope_name else {}
                for k, v in resource_attrs.items():
                    merged_attrs[f"resource.{k}"] = v
                merged_attrs.update(attrs)

                span_obj = SpanIngest(
                    span_id=span_id,
                    run_id=trace_id,
                    parent_span_id=parent_uuid,
                    name=name,
                    kind=kind,
                    status=status_str,  # type: ignore[arg-type]
                    start_time=start,
                    end_time=end,
                    model=_resolve_model(attrs),
                    temperature=_resolve_temperature(attrs),
                    inputs=inputs,
                    outputs=outputs,
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                    total_tokens=total_tok,
                    cost_usd=None,
                    error_kind=error_kind,
                    error_message=error_message,
                    attributes=merged_attrs,
                )

                bucket = buckets.setdefault(
                    trace_id,
                    {
                        "spans": [],
                        "min_start": start,
                        "max_end": end or start,
                        "root_span": None,
                        "root_depth": None,
                    },
                )
                bucket["spans"].append(span_obj)
                if start < bucket["min_start"]:
                    bucket["min_start"] = start
                cur_end = end or start
                if cur_end > bucket["max_end"]:
                    bucket["max_end"] = cur_end
                # Track the root: prefer a span with no parent; if
                # ambiguous, the earliest-starting wins.
                if parent_uuid is None and (
                    bucket["root_span"] is None or start < bucket["root_span"].start_time
                ):
                    bucket["root_span"] = span_obj

    runs: list[RunIngest] = []
    for trace_id, bucket in buckets.items():
        spans: list[SpanIngest] = bucket["spans"]
        if not spans:
            continue
        root = bucket["root_span"] or spans[0]
        # End-user identity is a run-level, root-span-sourced concern: the
        # human the agent serves. Read it from the root span's attributes
        # via the ordered fallback chain (enduser.id / user.id / ...).
        end_user_id = _first_str(root.attributes, _END_USER_ID_KEYS)
        # Session/thread key groups multi-turn runs. Prefer the root span,
        # but OpenInference stamps session.id on every span in the session
        # while our synthesized root may be a bare chain span, so fall back
        # to any span carrying it rather than lose the grouping.
        session_id = _first_str(root.attributes, _SESSION_ID_KEYS)
        if session_id is None:
            for s in spans:
                session_id = _first_str(s.attributes, _SESSION_ID_KEYS)
                if session_id is not None:
                    break
        # If the trace has an error span, the run is error; else ok.
        has_error = any(s.status == "error" for s in spans)
        # Token / cost totals aggregate the LLM spans for the run.
        prompt_tot = sum((s.prompt_tokens or 0) for s in spans if s.kind == "llm")
        comp_tot = sum((s.completion_tokens or 0) for s in spans if s.kind == "llm")
        total_tot = sum((s.total_tokens or 0) for s in spans if s.kind == "llm")
        runs.append(
            RunIngest(
                run_id=trace_id,
                parent_run_id=None,
                name=root.name,
                kind=root.kind,
                status="error" if has_error else "ok",
                sdk="otel",
                start_time=bucket["min_start"],
                end_time=bucket["max_end"],
                inputs=root.inputs,
                outputs=root.outputs,
                session_id=session_id,
                end_user_id=end_user_id,
                prompt_tokens=prompt_tot or None,
                completion_tokens=comp_tot or None,
                total_tokens=total_tot or None,
                cost_usd=None,
                tags=[],
                metadata={"otel": True},
                spans=spans,
            )
        )

    return runs, skipped


# ---------------------------------------------------------------------------
# Protobuf decode
# ---------------------------------------------------------------------------


def _b64_id_to_hex(value: Any) -> Any:
    """MessageToDict base64-encodes proto ``bytes`` ids; rewrite to hex.

    Leaves anything that isn't a decodable base64 string untouched so the
    downstream ``_trace_id_to_uuid`` / ``_span_id_to_uuid`` length checks make
    the final accept/skip decision (ER-23: never crash the batch on one bad id).
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        return base64.b64decode(value, validate=True).hex()
    except (ValueError, TypeError):
        return value


def _normalize_protobuf_ids(payload: dict[str, Any]) -> None:
    """Walk resourceSpans→scopeSpans→spans, rewriting id bytes to hex in place."""
    for rs in payload.get("resourceSpans") or []:
        if not isinstance(rs, dict):
            continue
        for ss in rs.get("scopeSpans") or []:
            if not isinstance(ss, dict):
                continue
            for span in ss.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                if "traceId" in span:
                    span["traceId"] = _b64_id_to_hex(span["traceId"])
                if "spanId" in span:
                    span["spanId"] = _b64_id_to_hex(span["spanId"])
                if "parentSpanId" in span:
                    span["parentSpanId"] = _b64_id_to_hex(span["parentSpanId"])


def _decode_protobuf_body(raw: bytes) -> dict[str, Any]:
    """Parse an OTLP ExportTraceServiceRequest into the JSON dict shape.

    ``MessageToDict`` yields the same camelCase keys (``resourceSpans``,
    ``scopeSpans``, ``startTimeUnixNano``, attribute ``stringValue`` etc.) the
    JSON path already consumes; we then hex-normalize the id byte fields.
    """
    req_pb = ExportTraceServiceRequest()
    try:
        req_pb.ParseFromString(raw)
    except DecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "malformed OTLP protobuf payload",
        ) from exc
    payload = MessageToDict(req_pb)
    _normalize_protobuf_ids(payload)
    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/v1/traces",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestAck,
)
async def otel_traces(
    request: Request,
    ctx: TenantContext = Depends(enforce_quota),
) -> IngestAck:
    """OTLP HTTP intake (JSON and protobuf).

    A stock OTel HTTP exporter posts ``application/x-protobuf`` to
    ``/v1/traces``; manual/legacy setups post ``application/json``. We
    dispatch on Content-Type, translate to the same ``IngestBatch``, and
    enqueue on the same Redis queue as native ingest.
    """
    content_type = request.headers.get("content-type", "")
    raw = await request.body()
    if "protobuf" in content_type:
        body = _decode_protobuf_body(raw)
    else:
        try:
            body = orjson.loads(raw) if raw else {}
        except orjson.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "invalid JSON body",
            ) from exc
        if not isinstance(body, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "OTLP payload must be a JSON object",
            )

    if "resourceSpans" not in body:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OTLP payload must include 'resourceSpans'",
        )

    runs, skipped = _translate_spans(body)
    if not runs:
        return IngestAck(accepted_runs=0, accepted_spans=0)

    batch = IngestBatch(sdk="otel", runs=runs)
    envelope: dict[str, Any] = {
        "org_id": str(ctx.org_id),
        "workspace_id": str(ctx.workspace_id),
        "project_id": str(ctx.project_id),
        "api_key_id": str(ctx.api_key_id),
        "received_at": datetime.now(UTC).isoformat(),
        "source": "otel",
        "payload": batch.model_dump(mode="json"),
    }
    enqueue: IngestEnqueue = request.app.state.enqueue
    redactor: Redactor = request.app.state.redactor
    counts = redactor.redact_envelope(envelope)
    if counts:
        log.info(
            "redacted",
            project_id=envelope["project_id"],
            counts=dict(counts),
        )
    serialized = serialize_batch(envelope)
    await enqueue.enqueue(serialized, org_id=ctx.org_id)

    accepted_spans = sum(len(r.spans) for r in runs)
    quota_meter: QuotaMeter = request.app.state.quota_meter
    try:
        await quota_meter.record(
            org_id=ctx.org_id, meter=INGEST_GATING_METER, amount=accepted_spans, limit=-1
        )
        await quota_meter.record(
            org_id=ctx.org_id, meter="span_bytes", amount=len(serialized), limit=-1
        )
    except Exception:  # noqa: BLE001
        log.warning("quota record failed", org_id=str(ctx.org_id))
    if skipped:
        log.warning(
            "otel batch had skipped spans",
            project_id=envelope["project_id"],
            skipped=skipped,
            accepted_spans=accepted_spans,
        )
    return IngestAck(accepted_runs=len(runs), accepted_spans=accepted_spans)
