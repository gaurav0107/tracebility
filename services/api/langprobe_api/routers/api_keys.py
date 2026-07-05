"""API key management.

Keys are ``lt_<public_id>.<secret>``. We store ``public_id`` (lookup key) and
argon2id(``secret``) only. The plain key is shown to the user ONCE on create.
This matches Stripe's pattern and is what the ingest-api validates.

Per ER-20: revocation must be immediate. We set ``revoked_at = now()`` (the
source of truth), then publish the key's ``public_id`` on the resolver's
``apikey:invalidate`` pubsub channel so the ingest-api's resolver drops its
cached positive lookup at once. Without that publish a revoked key keeps
authenticating ingest until the resolver's positive-cache TTL (60s) expires,
because ``require_ingest_key`` only re-verifies ``secret_hash`` on a cache hit.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from uuid import UUID

import asyncpg
import structlog
from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, status
from langprobe_tenant.resolver import announce_invalidation
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user

log = structlog.get_logger("langprobe.api.api_keys")
router = APIRouter(prefix="/v1/api_keys", tags=["api_keys"])

_PH = PasswordHasher()
_PUBLIC_ID_BYTES = 8  # 16 hex chars
_SECRET_BYTES = 32


def _generate_public_id() -> str:
    return secrets.token_hex(_PUBLIC_ID_BYTES)


def _generate_secret() -> str:
    return secrets.token_urlsafe(_SECRET_BYTES)


class ApiKeyOut(BaseModel):
    id: UUID
    project_id: UUID
    public_id: str
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None


class ApiKeyCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["ingest:write"])
    expires_at: datetime | None = None


class ApiKeyCreateResponse(BaseModel):
    key: ApiKeyOut
    plaintext_key: str  # shown ONCE


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    request: Request,
    project_id: UUID,
    principal: Principal = Depends(require_user),
) -> list[ApiKeyOut]:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await pool.fetchval(
        "select workspace_id from project where id = $1 and deleted_at is null",
        project_id,
    )
    if workspace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin", "member"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, public_id, name, scopes, created_at,
               last_used_at, revoked_at, expires_at
        from api_key
        where project_id = $1
        order by created_at desc
        """,
        project_id,
    )
    return [ApiKeyOut(**dict(r)) for r in rows]


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    request: Request,
    body: ApiKeyCreate,
    principal: Principal = Depends(require_user),
) -> ApiKeyCreateResponse:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await pool.fetchval(
        "select workspace_id from project where id = $1 and deleted_at is null",
        body.project_id,
    )
    if workspace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )

    public_id = _generate_public_id()
    secret = _generate_secret()
    secret_hash = _PH.hash(secret)
    plaintext = f"lt_{public_id}.{secret}"

    row = await pool.fetchrow(
        """
        insert into api_key (
            project_id, public_id, secret_hash, name, scopes,
            created_by, expires_at
        )
        values ($1, $2, $3, $4, $5, $6, $7)
        returning id, project_id, public_id, name, scopes, created_at,
                  last_used_at, revoked_at, expires_at
        """,
        body.project_id,
        public_id,
        secret_hash,
        body.name,
        body.scopes,
        principal.user_id,
        body.expires_at,
    )
    assert row is not None
    api_key = ApiKeyOut(**dict(row))
    await audit.record(
        pool,
        principal=principal,
        action="api_key.create",
        target_kind="api_key",
        target_id=api_key.id,
        payload={"name": api_key.name, "scopes": api_key.scopes},
        request=request,
        workspace_id=workspace_id,
        project_id=api_key.project_id,
    )
    return ApiKeyCreateResponse(key=api_key, plaintext_key=plaintext)


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    request: Request,
    api_key_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        """
        select api_key.project_id, api_key.public_id, project.workspace_id
        from api_key
        join project on project.id = api_key.project_id
        where api_key.id = $1 and api_key.revoked_at is null
        """,
        api_key_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "api key not found or already revoked")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=row["workspace_id"],
        allowed=("owner", "admin"),
    )
    await pool.execute(
        "update api_key set revoked_at = now() where id = $1",
        api_key_id,
    )
    # Bust the resolver's positive cache immediately. Without this the key
    # keeps authenticating ingest for up to the positive-cache TTL (60s) after
    # revocation, because require_ingest_key only re-verifies secret_hash — not
    # revoked_at — on a cache hit. Best-effort: the row is already revoked in
    # postgres (source of truth) and the TTL is the backstop, so a redis blip
    # must not fail the revoke.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await announce_invalidation(redis, row["public_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "api key revoked but cache-invalidation publish failed",
                api_key_id=str(api_key_id),
                error=str(exc),
            )
    await audit.record(
        pool,
        principal=principal,
        action="api_key.revoke",
        target_kind="api_key",
        target_id=api_key_id,
        request=request,
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
    )
