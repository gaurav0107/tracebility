"""LangSmith shim: recover the thread key from metadata.

LangSmith has no top-level session field. The SDK groups runs into a thread
via a metadata key (`session_id`, `thread_id`, or `conversation_id`) carried in
`extra.metadata`. The shim must read those so a correctly-tagged LangSmith run
populates run.session_id and shows up under /runs?view=threads — matching
LangSmith's own Threads behavior.
"""

from __future__ import annotations

from uuid import uuid4

from langprobe_ingest.routers.langsmith_shim import _to_run_ingest


def _body(**extra_meta: str) -> dict:
    return {
        "id": str(uuid4()),
        "name": "chat",
        "run_type": "llm",
        "start_time": "2026-07-12T00:00:00Z",
        "extra": {"metadata": dict(extra_meta)},
    }


def test_session_id_from_metadata_session_id() -> None:
    run = _to_run_ingest(_body(session_id="s-1"))
    assert run.session_id == "s-1"


def test_session_id_from_metadata_thread_id() -> None:
    run = _to_run_ingest(_body(thread_id="t-1"))
    assert run.session_id == "t-1"


def test_session_id_from_metadata_conversation_id() -> None:
    run = _to_run_ingest(_body(conversation_id="c-1"))
    assert run.session_id == "c-1"


def test_session_id_priority_session_over_thread() -> None:
    run = _to_run_ingest(_body(session_id="s-1", thread_id="t-1"))
    assert run.session_id == "s-1"


def test_top_level_session_id_wins() -> None:
    body = _body(thread_id="t-1")
    body["session_id"] = "explicit"
    run = _to_run_ingest(body)
    assert run.session_id == "explicit"


def test_no_thread_key_leaves_none() -> None:
    run = _to_run_ingest(_body(foo="bar"))
    assert run.session_id is None
