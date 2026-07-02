"""Saved-view filters.kind allowlist.

`_validate_filters` HTTP 400s any filters.kind outside its literal
allowlist. Batch 1 added five new span kinds (workflow, task,
guardrail, evaluator, reranker); a saved view must be able to filter
on them, so the allowlist has to include them.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from langprobe_api.routers.saved_views import SavedViewFilters, _validate_filters


@pytest.mark.parametrize(
    "kind",
    ["agent", "chain", "llm", "tool", "retriever", "embedding", "parser"],
)
def test_accepts_existing_kinds(kind: str) -> None:
    _validate_filters(SavedViewFilters(kind=kind))


@pytest.mark.parametrize(
    "kind",
    ["workflow", "task", "guardrail", "evaluator", "reranker"],
)
def test_accepts_batch1_kinds(kind: str) -> None:
    """These were 400 before Batch 1 consumer sweep."""
    _validate_filters(SavedViewFilters(kind=kind))


def test_rejects_unknown_kind() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_filters(SavedViewFilters(kind="not-a-kind"))
    assert exc.value.status_code == 400


def test_none_kind_is_allowed() -> None:
    _validate_filters(SavedViewFilters(kind=None))
