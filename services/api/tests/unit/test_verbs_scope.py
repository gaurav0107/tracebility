"""Scope guard for the verb service layer (Task 2, D2-A).

``require_project_scope`` is the single chokepoint every verb calls
before touching data: it enforces that the ``project_id`` a caller
asked to operate on matches the ``project_id`` baked into their
``TenantContext`` (resolved from the API key at auth time). This module
is framework-agnostic — it raises a domain exception, not an HTTPException;
mapping ``ScopeError`` to a 403 is a later task's job (the HTTP router).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from langprobe_api.verbs.scope import ScopeError, require_project_scope
from langprobe_tenant.context import TenantContext


def _make_ctx(project_id) -> TenantContext:
    return TenantContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        project_id=project_id,
        api_key_id=uuid4(),
        plan="pro",
        scopes=frozenset({"verbs:*"}),
    )


def test_require_project_scope_passes_on_match():
    project_id = uuid4()
    ctx = _make_ctx(project_id)

    # Should not raise.
    require_project_scope(ctx, project_id)


def test_require_project_scope_raises_on_mismatch():
    ctx = _make_ctx(uuid4())
    other_project_id = uuid4()

    with pytest.raises(ScopeError):
        require_project_scope(ctx, other_project_id)
