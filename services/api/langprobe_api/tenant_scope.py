"""Per-request tenant scope for the api service.

Every route that reads or writes ClickHouse trace data must do it through a
``TenantScope`` so the (org_id, workspace_id, project_id) triple is enforced
in WHERE clauses. The Phase 12 property test asserts no router emits a CH
query that lacks ``org_id`` in its WHERE.

The scope is built ONCE per request — typically right after the existing
``_assert_project_access`` call in the router. We then route every CH read
through ``scope.query(...)`` instead of ``ch.query(...)``; the helper stitches
the org/workspace/project predicate onto the SQL and the parameter dict.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

from .auth import Principal, assert_workspace_role
from .clickhouse_client import ClickHouseQuery

_ORG_FILTER_SQL = "and org_id = {org_id:UUID}"


@dataclass(frozen=True, slots=True)
class TenantScope:
    """Resolved tuple for the current request. Immutable on purpose — it
    travels through query helpers and must not be mutated mid-flight."""

    org_id: UUID
    workspace_id: UUID
    project_id: UUID
    principal: Principal


async def resolve_tenant_ids(
    pool: asyncpg.Pool,
    project_id: UUID,
) -> tuple[UUID, UUID]:
    """Return ``(org_id, workspace_id)`` for a project.

    Write helper for ClickHouse inserts into tenant-aware tables (run, span,
    eval_score, dataset_item, ...). Every such insert must carry the
    ``(org_id, workspace_id)`` tuple so post-0006 rows land under the correct
    tenant instead of the zero-UUID default; see
    ``test_property_tenant_columns_on_insert.py``.

    This performs no RBAC — callers resolve the request principal's access
    separately (``resolve_project_scope`` / ``_assert_project_role``) before
    writing. It only maps project_id -> tenant tuple.
    """
    row = await pool.fetchrow(
        """
        select project.workspace_id, workspace.org_id
        from project
        join workspace on workspace.id = project.workspace_id
        where project.id = $1
        """,
        project_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return row["org_id"], row["workspace_id"]


async def resolve_project_scope(
    pool: asyncpg.Pool,
    project_id: UUID,
    principal: Principal,
    *,
    allowed_roles: Sequence[str] = ("owner", "admin", "member", "viewer"),
) -> TenantScope:
    """Look up org/workspace for a project and verify principal can see it.

    Replaces the pattern that used to live as ``_assert_project_access`` in
    individual routers — those callers should now use this directly so the
    triple is available for the CH WHERE clause."""
    row = await pool.fetchrow(
        """
        select project.workspace_id, workspace.org_id
        from project
        join workspace on workspace.id = project.workspace_id
        where project.id = $1 and project.deleted_at is null
        """,
        project_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    workspace_id = row["workspace_id"]
    org_id = row["org_id"]

    # Root principal bypasses RBAC for ops/diagnostics. Otherwise the
    # principal must have one of the allowed workspace roles.
    if not principal.is_root:
        await assert_workspace_role(
            pool,
            user_id=principal.user_id,
            workspace_id=workspace_id,
            allowed=tuple(allowed_roles),
        )

    return TenantScope(
        org_id=org_id,
        workspace_id=workspace_id,
        project_id=project_id,
        principal=principal,
    )


# ---------------------------------------------------------------------------
# Scoped query helpers
# ---------------------------------------------------------------------------

# Regex to detect whether a SQL string already filters on org_id.
# Conservative — matches "org_id = " or "org_id =\n" with optional whitespace.
_HAS_ORG_FILTER = re.compile(r"\borg_id\s*=", re.IGNORECASE)


class ScopedClickHouse:
    """Wrap a ``ClickHouseQuery`` with a tenant scope. All routes that touch
    trace tables go through this so the org filter is non-bypassable.

    The helper auto-injects ``and org_id = {org_id:UUID}`` if the caller
    forgot. It also supplies ``project_id`` / ``workspace_id`` / ``org_id``
    in the parameters dict if the SQL references them.
    """

    def __init__(self, ch: ClickHouseQuery, scope: TenantScope) -> None:
        self._ch = ch
        self._scope = scope

    @property
    def scope(self) -> TenantScope:
        return self._scope

    def _inject(self, sql: str, params: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        merged: dict[str, Any] = dict(params or {})
        merged.setdefault("org_id", str(self._scope.org_id))
        merged.setdefault("workspace_id", str(self._scope.workspace_id))
        merged.setdefault("project_id", str(self._scope.project_id))

        # Belt-and-braces. If the SQL already has ``org_id = ...``, trust it.
        # Otherwise inject the predicate into the WHERE clause. We require
        # the caller's SQL to already contain ``where`` somewhere; if not,
        # raise — that's a mistake the property test will catch in CI but
        # we'd rather fail loud at request time too.
        if _HAS_ORG_FILTER.search(sql):
            return sql, merged
        if " where " not in sql.lower():
            raise ValueError(
                "ScopedClickHouse: SQL must contain a WHERE clause to be tenant-scoped",
            )
        # Append after the existing WHERE. The simplest stable placement is
        # right at the end of the predicate chain — before any GROUP BY /
        # ORDER BY / LIMIT clause.
        sql_with_filter = _append_predicate(sql, _ORG_FILTER_SQL)
        return sql_with_filter, merged

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        sql, params = self._inject(sql, parameters)
        return await self._ch.query(sql, parameters=params)

    async def command(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        sql, params = self._inject(sql, parameters)
        await self._ch.command(sql, parameters=params)


_TRAILING_CLAUSES = re.compile(
    r"\b(group\s+by|order\s+by|limit|having)\b",
    re.IGNORECASE,
)


def _append_predicate(sql: str, predicate: str) -> str:
    """Insert ``predicate`` after the WHERE chain but before any
    GROUP BY / ORDER BY / LIMIT / HAVING. Whitespace-preserving."""
    match = _TRAILING_CLAUSES.search(sql)
    if match is None:
        return f"{sql.rstrip()} {predicate}"
    head = sql[: match.start()].rstrip()
    tail = sql[match.start() :]
    return f"{head} {predicate} {tail}"
