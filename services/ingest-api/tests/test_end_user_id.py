"""End-user identity: distinguish the operator from the agent's end user.

Covers the schema (RunIngest accepts end_user_id / end_user_metadata) and the
OTel shim stamping end_user_id onto the synthesized Run from root-span
attributes via the OTel/OpenInference fallback chain (enduser.id / user.id / ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from langprobe_ingest.routers.otel import _translate_spans
from langprobe_ingest.schemas import RunIngest


def test_run_ingest_accepts_end_user_fields() -> None:
    run = RunIngest(
        run_id=uuid4(),
        name="r",
        kind="chain",
        start_time=datetime.now(UTC),
        end_user_id="customer-42",
        end_user_metadata={"plan": "pro"},
    )
    assert run.end_user_id == "customer-42"
    assert run.end_user_metadata == {"plan": "pro"}


def test_run_ingest_end_user_defaults_none() -> None:
    run = RunIngest(
        run_id=uuid4(),
        name="r",
        kind="chain",
        start_time=datetime.now(UTC),
    )
    assert run.end_user_id is None
    assert run.end_user_metadata is None


def _otlp_payload(*attributes: dict) -> dict:
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "b7e22605f9bc40bb8089607b3ba0ada3",
                                "spanId": "fa18a08d75bd4aa2",
                                "name": "agent.run",
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "status": {"code": "OK"},
                                "attributes": list(attributes),
                            }
                        ]
                    }
                ]
            }
        ]
    }


def test_otel_stamps_end_user_id_from_enduser_id() -> None:
    payload = _otlp_payload(
        {"key": "enduser.id", "value": {"stringValue": "end-user-9"}},
    )
    runs, skipped = _translate_spans(payload)
    assert skipped == 0
    assert len(runs) == 1
    assert runs[0].end_user_id == "end-user-9"


def test_otel_stamps_end_user_id_from_user_id_fallback() -> None:
    payload = _otlp_payload(
        {"key": "user.id", "value": {"stringValue": "u-123"}},
    )
    runs, _ = _translate_spans(payload)
    assert runs[0].end_user_id == "u-123"


def test_otel_no_end_user_id_leaves_none() -> None:
    payload = _otlp_payload(
        {"key": "llm.model_name", "value": {"stringValue": "gpt-4o-mini"}},
    )
    runs, _ = _translate_spans(payload)
    assert runs[0].end_user_id is None
