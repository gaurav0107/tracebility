"""OTLP protobuf intake on ``POST /v1/traces``.

The endpoint must accept BOTH ``application/json`` (the shape it has always
served) AND ``application/x-protobuf`` (what a stock
``opentelemetry-exporter-otlp-proto-http`` exporter sends). Both wire formats
must translate to the identical native ``IngestBatch`` and return a 202 with
the same accepted counts.

These tests stand up the real ``otel.router`` on a bare FastAPI app with the
``enforce_quota`` dependency overridden and ``app.state`` stubbed, so no
Postgres/Redis/ClickHouse is required. The enqueue is captured in-memory so we
can assert what actually got translated.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langprobe_ingest.limits import enforce_quota
from langprobe_ingest.redactor import Redactor
from langprobe_ingest.routers import otel
from langprobe_tenant import TenantContext
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
)

_ORG = UUID("11111111-1111-1111-1111-111111111111")
_WS = UUID("22222222-2222-2222-2222-222222222222")
_PROJ = UUID("33333333-3333-3333-3333-333333333333")
_KEY = UUID("44444444-4444-4444-4444-444444444444")

_TRACE_ID = bytes.fromhex("b7e22605f9bc40bb8089607b3ba0ada3")
_SPAN_ID = bytes.fromhex("fa18a08d75bd4aa2")


class _StubEnqueue:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def enqueue(self, batch: bytes, *, org_id: UUID) -> None:
        self.calls.append(batch)


class _StubQuotaMeter:
    async def record(self, *, org_id: UUID, meter: str, amount: int, limit: int) -> None:
        return None


def _build_app() -> tuple[FastAPI, _StubEnqueue]:
    app = FastAPI()
    app.include_router(otel.router)
    enqueue = _StubEnqueue()
    app.state.enqueue = enqueue
    app.state.redactor = Redactor(enabled=False)
    app.state.quota_meter = _StubQuotaMeter()

    def _fake_ctx() -> TenantContext:
        return TenantContext(
            org_id=_ORG,
            workspace_id=_WS,
            project_id=_PROJ,
            api_key_id=_KEY,
            plan="free",
            scopes=frozenset({"ingest:write"}),
        )

    app.dependency_overrides[enforce_quota] = _fake_ctx
    return app, enqueue


def _kv(key: str, value: AnyValue) -> KeyValue:
    return KeyValue(key=key, value=value)


def _llm_export_request() -> ExportTraceServiceRequest:
    """One resource → one scope → one LLM span with tokens/model/status ok."""
    span = Span(
        trace_id=_TRACE_ID,
        span_id=_SPAN_ID,
        name="chat gpt-4o-mini",
        start_time_unix_nano=1_700_000_000_000_000_000,
        end_time_unix_nano=1_700_000_001_000_000_000,
        status=Status(code=Status.STATUS_CODE_OK),
        attributes=[
            _kv("openinference.span.kind", AnyValue(string_value="LLM")),
            _kv("llm.model_name", AnyValue(string_value="gpt-4o-mini")),
            _kv("llm.token_count.prompt", AnyValue(int_value=1000)),
            _kv("llm.token_count.completion", AnyValue(int_value=500)),
            _kv("input.value", AnyValue(string_value="hello")),
            _kv("output.value", AnyValue(string_value="hi there")),
        ],
    )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                scope_spans=[
                    ScopeSpans(spans=[span]),
                ],
            ),
        ]
    )


def _decode_envelope(raw: bytes) -> dict[str, Any]:
    import orjson

    return orjson.loads(raw)


def test_protobuf_export_ingests_and_returns_202() -> None:
    app, enqueue = _build_app()
    client = TestClient(app)

    body = _llm_export_request().SerializeToString()
    resp = client.post(
        "/v1/traces",
        content=body,
        headers={"content-type": "application/x-protobuf"},
    )

    assert resp.status_code == 202, resp.text
    ack = resp.json()
    assert ack == {"accepted_runs": 1, "accepted_spans": 1}

    # The translated batch must carry the correct hex-decoded run id (matching
    # the original trace id) and the LLM span's model/tokens.
    assert len(enqueue.calls) == 1
    env = _decode_envelope(enqueue.calls[0])
    runs = env["payload"]["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert UUID(run["run_id"]) == UUID(_TRACE_ID.hex())
    assert len(run["spans"]) == 1
    span = run["spans"][0]
    assert span["model"] == "gpt-4o-mini"
    assert span["kind"] == "llm"
    assert span["prompt_tokens"] == 1000
    assert span["completion_tokens"] == 500
    assert span["inputs"] == "hello"
    assert span["outputs"] == "hi there"


def test_protobuf_error_status_span_maps_to_error() -> None:
    app, enqueue = _build_app()
    client = TestClient(app)

    span = Span(
        trace_id=_TRACE_ID,
        span_id=_SPAN_ID,
        name="chat gpt-4o-mini",
        start_time_unix_nano=1_700_000_000_000_000_000,
        end_time_unix_nano=1_700_000_001_000_000_000,
        status=Status(code=Status.STATUS_CODE_ERROR, message="boom"),
        attributes=[
            _kv("openinference.span.kind", AnyValue(string_value="LLM")),
        ],
    )
    req = ExportTraceServiceRequest(
        resource_spans=[ResourceSpans(scope_spans=[ScopeSpans(spans=[span])])]
    )

    resp = client.post(
        "/v1/traces",
        content=req.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 202, resp.text

    env = _decode_envelope(enqueue.calls[0])
    run = env["payload"]["runs"][0]
    assert run["status"] == "error"
    assert run["spans"][0]["status"] == "error"
    assert run["spans"][0]["error_message"] == "boom"


def test_json_path_still_works() -> None:
    app, enqueue = _build_app()
    client = TestClient(app)

    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": _TRACE_ID.hex(),
                                "spanId": _SPAN_ID.hex(),
                                "name": "chat gpt-4o-mini",
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "status": {"code": "OK"},
                                "attributes": [
                                    {
                                        "key": "openinference.span.kind",
                                        "value": {"stringValue": "LLM"},
                                    },
                                    {
                                        "key": "llm.model_name",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    resp = client.post("/v1/traces", json=payload)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted_runs": 1, "accepted_spans": 1}

    env = _decode_envelope(enqueue.calls[0])
    run = env["payload"]["runs"][0]
    assert UUID(run["run_id"]) == UUID(_TRACE_ID.hex())
    assert run["spans"][0]["model"] == "gpt-4o-mini"


def test_missing_resource_spans_json_returns_400() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    resp = client.post("/v1/traces", json={"not": "otlp"})
    assert resp.status_code == 400


def test_empty_protobuf_request_returns_400() -> None:
    # An empty ExportTraceServiceRequest has no resourceSpans field at all, so
    # MessageToDict yields {} — same as a JSON body with no resourceSpans, which
    # the handler rejects with 400. The protobuf path must preserve that.
    app, _ = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/v1/traces",
        content=ExportTraceServiceRequest().SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 400, resp.text


def test_malformed_protobuf_returns_400() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/v1/traces",
        content=b"\xff\xff not a valid protobuf \x00\x01",
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 400, resp.text
