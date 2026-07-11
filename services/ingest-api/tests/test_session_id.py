"""Session/thread grouping: the OTLP shim must stamp session_id onto the run.

A run only rolls up under /runs?view=threads when its session_id column is
non-empty (the threads query filters `session_id != '' and is not null`).
OpenInference emits `session.id` on its spans and OTel GenAI uses
`gen_ai.conversation.id`; without extracting one of these the synthesized run
lands with session_id = NULL and the Threads view is always empty.

Regression: before the fix, `_translate_spans` never set session_id, so every
OTel-instrumented trace was invisible under Threads.
"""

from __future__ import annotations

from langprobe_ingest.routers.otel import _SESSION_ID_KEYS, _translate_spans


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


def test_session_id_chain_present_in_mapping() -> None:
    # The fallback chain must cover OpenInference + OTel GenAI conventions.
    assert "session.id" in _SESSION_ID_KEYS
    assert "gen_ai.conversation.id" in _SESSION_ID_KEYS


def test_otel_stamps_session_id_from_openinference() -> None:
    payload = _otlp_payload(
        {"key": "session.id", "value": {"stringValue": "sess-abc"}},
    )
    runs, skipped = _translate_spans(payload)
    assert skipped == 0
    assert len(runs) == 1
    assert runs[0].session_id == "sess-abc"


def test_otel_stamps_session_id_from_gen_ai_conversation_fallback() -> None:
    payload = _otlp_payload(
        {"key": "gen_ai.conversation.id", "value": {"stringValue": "conv-9"}},
    )
    runs, _ = _translate_spans(payload)
    assert runs[0].session_id == "conv-9"


def test_otel_no_session_id_leaves_none() -> None:
    payload = _otlp_payload(
        {"key": "llm.model_name", "value": {"stringValue": "gpt-4o-mini"}},
    )
    runs, _ = _translate_spans(payload)
    assert runs[0].session_id is None


def test_otel_reads_session_id_from_non_root_span() -> None:
    # The synthesized root may be a bare chain span; session.id often rides on
    # a child LLM span. We must still recover the grouping key.
    payload = {
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
                                "endTimeUnixNano": "1700000002000000000",
                                "status": {"code": "OK"},
                                "attributes": [],
                            },
                            {
                                "traceId": "b7e22605f9bc40bb8089607b3ba0ada3",
                                "spanId": "aa18a08d75bd4aa3",
                                "parentSpanId": "fa18a08d75bd4aa2",
                                "name": "chat",
                                "startTimeUnixNano": "1700000000500000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "status": {"code": "OK"},
                                "attributes": [
                                    {
                                        "key": "session.id",
                                        "value": {"stringValue": "sess-child"},
                                    }
                                ],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    runs, _ = _translate_spans(payload)
    assert len(runs) == 1
    assert runs[0].session_id == "sess-child"
