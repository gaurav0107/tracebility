"""Workspace switcher endpoint: list the workspaces the principal can see.

Multi-tenancy spec §7.3: the top nav grows a workspace switcher. The
backend stays thin — most routers already scope by ``project_id``, which
fully determines workspace + org. The UI just needs a single endpoint
to populate the dropdown (workspace name + slug + the user's role inside
each workspace), and a no-op POST so the action is auditable.

Switcher flow:
  1. UI calls GET /v1/me/workspaces -> dropdown renders.
  2. UI persists active workspace_id in localStorage and threads it
     into per-workspace requests as a query parameter.
  3. UI calls POST /v1/me/workspaces/{workspace_id}/select to record
     the change in the audit log (so an admin can see who pivoted into
     a workspace and when).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import Principal, require_user

router = APIRouter(prefix="/v1/me", tags=["me"])


class WorkspaceItem(BaseModel):
    workspace_id: UUID
    workspace_slug: str
    workspace_name: str
    org_id: UUID
    org_slug: str
    org_name: str
    role: str


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceItem]


@router.get("/workspaces", response_model=WorkspaceListResponse)
async def list_my_workspaces(
    request: Request,
    principal: Principal = Depends(require_user),
) -> WorkspaceListResponse:
    pool: asyncpg.Pool = request.app.state.pg
    rows = await pool.fetch(
        """
        select workspace.id   as workspace_id,
               workspace.slug as workspace_slug,
               workspace.name as workspace_name,
               org.id         as org_id,
               org.slug       as org_slug,
               org.name       as org_name,
               workspace_member.role as role
        from workspace_member
        join workspace on workspace.id = workspace_member.workspace_id
        join org       on org.id = workspace.org_id
        where workspace_member.user_id = $1
          and workspace.deleted_at is null
          and org.deleted_at is null
        order by org.name, workspace.name
        """,
        principal.user_id,
    )
    return WorkspaceListResponse(
        items=[
            WorkspaceItem(
                workspace_id=row["workspace_id"],
                workspace_slug=row["workspace_slug"],
                workspace_name=row["workspace_name"],
                org_id=row["org_id"],
                org_slug=row["org_slug"],
                org_name=row["org_name"],
                role=row["role"],
            )
            for row in rows
        ]
    )


@router.post(
    "/workspaces/{workspace_id}/select",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def select_workspace(
    request: Request,
    workspace_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    """Audit-only: record that this principal switched into ``workspace_id``.

    The actual 'active workspace' state lives on the client. We just want
    a record an admin can grep when investigating activity."""
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        """
        select workspace_member.role, workspace.org_id
        from workspace_member
        join workspace on workspace.id = workspace_member.workspace_id
        where workspace_member.user_id = $1 and workspace.id = $2
          and workspace.deleted_at is null
        """,
        principal.user_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not visible")

    # Audit: workspace.select isn't yet a member of the EventType lexicon
    # (spec §5.7 names the ones we care about for compliance). We still
    # log it via the legacy postgres audit_log so the admin UI can render
    # 'who pivoted where' without us inventing a new event type. A future
    # iteration can promote this to a ClickHouse audit row if pivot
    # frequency makes the postgres table noisy.
    await pool.execute(
        """
        insert into audit_log
            (org_id, workspace_id, actor_user_id, action, target_kind, target_id, payload)
        values ($1, $2, $3, 'workspace.select', 'workspace', $2, '{}'::jsonb)
        """,
        row["org_id"],
        workspace_id,
        principal.user_id,
    )
