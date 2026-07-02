"""Expanded span-kind coverage.

The ingest schema's ``RunKind`` Literal is the single source of truth for which
span kinds ingest accepts. This asserts the five new kinds (workflow, task,
guardrail, evaluator, reranker) validate on both ``SpanIngest`` and
``RunIngest``, and that the pre-existing kinds keep working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langprobe_ingest.schemas import RunIngest, SpanIngest

_NEW_KINDS = ("workflow", "task", "guardrail", "evaluator", "reranker")
_OLD_KINDS = ("llm", "chain", "tool", "agent", "retriever", "embedding", "parser")


@pytest.mark.parametrize("kind", _NEW_KINDS + _OLD_KINDS)
def test_span_ingest_accepts_kind(kind: str) -> None:
    span = SpanIngest(
        span_id=uuid4(),
        run_id=uuid4(),
        name="s",
        kind=kind,  # type: ignore[arg-type]
        start_time=datetime.now(UTC),
    )
    assert span.kind == kind


@pytest.mark.parametrize("kind", _NEW_KINDS + _OLD_KINDS)
def test_run_ingest_accepts_kind(kind: str) -> None:
    run = RunIngest(
        run_id=uuid4(),
        name="r",
        kind=kind,  # type: ignore[arg-type]
        start_time=datetime.now(UTC),
    )
    assert run.kind == kind


def test_unknown_kind_still_rejected() -> None:
    with pytest.raises(ValueError):
        SpanIngest(
            span_id=uuid4(),
            run_id=uuid4(),
            name="s",
            kind="totally-bogus",  # type: ignore[arg-type]
            start_time=datetime.now(UTC),
        )
