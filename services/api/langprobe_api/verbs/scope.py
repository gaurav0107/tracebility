"""Per-call project-scope guard for the verb service layer (Task 2, D2-A).

Every verb accepts a caller-supplied ``project_id`` (or a value that
implies one, e.g. a ``cluster_id`` resolved to a project) alongside the
``TenantContext`` resolved from the caller's API key. ``require_project_scope``
is the single chokepoint that enforces the two agree, so a valid API key
for project A can never be used to read or mutate project B's data.

Framework-agnostic on purpose: this module raises a plain domain
exception (``ScopeError``), not ``fastapi.HTTPException``. The HTTP
router (a later task) maps ``ScopeError`` to a 403; the MCP adapter
(also later) can map it to whatever error shape MCP expects. Neither
FastAPI nor MCP should be imported here.
"""

from __future__ import annotations

from uuid import UUID

from langprobe_tenant.context import TenantContext


class ScopeError(Exception):
    """Raised when a verb call's requested project_id is out of scope
    for the caller's TenantContext."""


def require_project_scope(ctx: TenantContext, project_id: UUID) -> None:
    """Raise :class:`ScopeError` unless ``project_id`` matches ``ctx.project_id``."""
    if project_id != ctx.project_id:
        raise ScopeError(
            f"project_id {project_id} is out of scope for this API key "
            f"(scoped to project {ctx.project_id})"
        )
