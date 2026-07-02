"""SCIM 2.0 provisioning for workspace_member.

Implements the slice of SCIM 2.0 (RFC 7644) that Okta / Azure AD /
JumpCloud actually drive in their default User-sync configurations:

  - GET    /scim/v2/ServiceProviderConfig
  - GET    /scim/v2/ResourceTypes
  - GET    /scim/v2/Schemas
  - GET    /scim/v2/Users (with filter, startIndex, count)
  - POST   /scim/v2/Users
  - GET    /scim/v2/Users/{id}
  - PUT    /scim/v2/Users/{id}
  - PATCH  /scim/v2/Users/{id}
  - DELETE /scim/v2/Users/{id}     (deactivate; soft-deletes)

Auth: a workspace-scoped bearer token (`tbs_<...>`). The token's
workspace is the tenancy boundary — every Users/{id} lookup is
scoped to it via `scim_user_mapping`. Tokens are minted + revoked
through small admin endpoints under `/v1/auth/scim-tokens`.

Group provisioning is deliberately deferred. Most IdPs default to
user-only sync; the moment we hear "we need group-driven roles",
adding `/scim/v2/Groups` is mechanical and ships in a follow-up
without changing the user surface.

V1 honest scope:
- We don't store SCIM `meta.location` URLs in the DB. We render them
  per-request from the public origin so a host swap doesn't strand
  rows with stale references.
- PATCH supports the common ops (`replace`, `add`, `remove`) on
  `userName`, `name.formatted`, `emails`, `active`, and `displayName`.
  Custom enterprise schema attributes for role mapping are accepted
  via `urn:ietf:params:scim:schemas:extension:enterprise:2.0:User`.
- Filter parsing is the narrow set Okta sends: `userName eq "x"`,
  `externalId eq "x"`, `id eq "x"`. Anything fancier returns 400.

ER-23 / ER-10: SCIM writes are audit-fail-closed and never silently
drop a user. Failed local writes (e.g. duplicate email) propagate as
SCIM-shaped errors so the IdP marks the row in error rather than
silently believing it shipped.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from .. import audit
from ..auth import (
    Principal,
    assert_workspace_role,
    hash_password,
    require_user,
    verify_password,
)

log = structlog.get_logger("langprobe.api.scim")

# Two routers: the SCIM-spec endpoints (`/scim/v2/...`) and the
# admin-side token CRUD (`/v1/auth/scim-tokens`). Both are mounted
# from app.py.
router = APIRouter(prefix="/scim/v2", tags=["scim"])
admin_router = APIRouter(prefix="/v1/auth/scim-tokens", tags=["scim-admin"])

_TOKEN_PREFIX = "tbs_"
_FILTER_RE = re.compile(
    r'^(?P<attr>userName|externalId|id)\s+eq\s+"(?P<value>[^"]*)"$',
    re.IGNORECASE,
)
_USER_RESOURCE_TYPE = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
    "id": "User",
    "name": "User",
    "endpoint": "/Users",
    "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
    "schemaExtensions": [
        {
            "schema": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            "required": False,
        }
    ],
}


# ---------------------------------------------------------------------------
# Auth dependency: SCIM bearer token → SCIMContext(workspace_id)
# ---------------------------------------------------------------------------


class SCIMContext(BaseModel):
    workspace_id: UUID
    token_id: UUID


async def require_scim_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> SCIMContext:
    """Resolve a `tbs_*` bearer token to a workspace context."""
    if not authorization or not authorization.lower().startswith("bearer "):
        _scim_error(401, "Missing bearer token")
    token = authorization[len("bearer ") :].strip()
    if not token.startswith(_TOKEN_PREFIX):
        _scim_error(401, "Invalid SCIM token format")

    raw = token[len(_TOKEN_PREFIX) :]
    if "." not in raw:
        _scim_error(401, "Invalid SCIM token format")
    public_id, secret = raw.split(".", 1)
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        """
        select id, workspace_id, secret_hash, revoked_at
          from workspace_scim_token
         where public_id = $1
        """,
        public_id,
    )
    if row is None or row["revoked_at"] is not None:
        _scim_error(401, "Invalid or revoked SCIM token")
    if not verify_password(row["secret_hash"], secret):
        _scim_error(401, "Invalid SCIM token")
    # Update last_used_at best-effort (don't fail the request on a
    # transient write error).
    try:
        await pool.execute(
            "update workspace_scim_token set last_used_at = now() where id = $1",
            row["id"],
        )
    except asyncpg.PostgresError:  # pragma: no cover
        pass
    return SCIMContext(workspace_id=row["workspace_id"], token_id=row["id"])


def _scim_error(code: int, detail: str, scim_type: str | None = None) -> None:
    body: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    raise HTTPException(status_code=code, detail=body)


# ---------------------------------------------------------------------------
# Token admin (cookie-auth, owner/admin only)
# ---------------------------------------------------------------------------


class SCIMTokenOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    public_id: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class SCIMTokenCreated(SCIMTokenOut):
    plaintext: str = Field(
        description="Full token; shown once. Save it now.",
    )


class SCIMTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@admin_router.get("", response_model=list[SCIMTokenOut])
async def list_tokens(
    request: Request,
    workspace_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[SCIMTokenOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )
    rows = await pool.fetch(
        """
        select id, workspace_id, name, public_id, created_at,
               last_used_at, revoked_at
          from workspace_scim_token
         where workspace_id = $1
         order by created_at desc
        """,
        workspace_id,
    )
    return [SCIMTokenOut(**dict(r)) for r in rows]


@admin_router.post(
    "",
    response_model=SCIMTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    request: Request,
    body: SCIMTokenCreate,
    workspace_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> SCIMTokenCreated:
    pool: asyncpg.Pool = request.app.state.pg
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )
    public_id = secrets.token_hex(8)
    secret = secrets.token_hex(24)
    secret_hash = hash_password(secret)
    row = await pool.fetchrow(
        """
        insert into workspace_scim_token (
            workspace_id, name, public_id, secret_hash, created_by
        )
        values ($1, $2, $3, $4, $5)
        returning id, workspace_id, name, public_id, created_at,
                  last_used_at, revoked_at
        """,
        workspace_id,
        body.name,
        public_id,
        secret_hash,
        principal.user_id,
    )
    assert row is not None
    plaintext = f"{_TOKEN_PREFIX}{public_id}.{secret}"
    await audit.record(
        pool,
        principal=principal,
        action="scim_token.create",
        target_kind="workspace_scim_token",
        target_id=row["id"],
        payload={"name": body.name},
        request=request,
        workspace_id=workspace_id,
    )
    return SCIMTokenCreated(plaintext=plaintext, **dict(row))


@admin_router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    request: Request,
    token_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        "select workspace_id from workspace_scim_token where id = $1",
        token_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=row["workspace_id"],
        allowed=("owner", "admin"),
    )
    await pool.execute(
        "update workspace_scim_token set revoked_at = now() where id = $1",
        token_id,
    )
    await audit.record(
        pool,
        principal=principal,
        action="scim_token.revoke",
        target_kind="workspace_scim_token",
        target_id=token_id,
        payload={},
        request=request,
        workspace_id=row["workspace_id"],
    )


# ---------------------------------------------------------------------------
# SCIM discovery endpoints (no auth required; static)
# ---------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def service_provider_config() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://github.com/langprobe/langprobe",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Workspace-scoped SCIM token (tbs_*).",
                "primary": True,
            }
        ],
    }


@router.get("/ResourceTypes")
async def resource_types() -> dict[str, Any]:
    return _list_response([_USER_RESOURCE_TYPE])


@router.get("/ResourceTypes/User")
async def resource_type_user() -> dict[str, Any]:
    return _USER_RESOURCE_TYPE


@router.get("/Schemas")
async def schemas() -> dict[str, Any]:
    return _list_response([_USER_SCHEMA, _ENTERPRISE_USER_SCHEMA])


# ---------------------------------------------------------------------------
# Users CRUD
# ---------------------------------------------------------------------------


@router.get("/Users")
async def list_users(
    request: Request,
    filter: str | None = Query(default=None, alias="filter"),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=0, le=200),
    ctx: SCIMContext = Depends(require_scim_token),
) -> dict[str, Any]:
    pool: asyncpg.Pool = request.app.state.pg
    where_sql = "m.workspace_id = $1"
    args: list[Any] = [ctx.workspace_id]

    if filter is not None:
        match = _FILTER_RE.match(filter.strip())
        if not match:
            _scim_error(
                400,
                "Unsupported filter; only 'userName eq', 'externalId eq', "
                "and 'id eq' are supported",
                scim_type="invalidFilter",
            )
        attr = match.group("attr").lower()
        value = match.group("value")
        if attr == "username":
            args.append(value.lower())
            where_sql += f" and lower(u.email) = ${len(args)}"
        elif attr == "externalid":
            args.append(value)
            where_sql += f" and m.external_id = ${len(args)}"
        elif attr == "id":
            try:
                args.append(UUID(value))
            except ValueError:
                _scim_error(400, "id is not a UUID", scim_type="invalidValue")
            where_sql += f" and m.id = ${len(args)}"

    total_row = await pool.fetchrow(
        f"""
        select count(*) as total
          from scim_user_mapping m
          join app_user u on u.id = m.user_id
         where {where_sql}
        """,
        *args,
    )
    total = int((total_row or {"total": 0})["total"])

    rows = await pool.fetch(
        f"""
        select m.id, m.external_id, m.workspace_id, m.role, m.active,
               m.created_at, m.updated_at,
               u.id as user_id, u.email, u.full_name
          from scim_user_mapping m
          join app_user u on u.id = m.user_id
         where {where_sql}
         order by m.created_at asc
         limit ${len(args) + 1} offset ${len(args) + 2}
        """,
        *args,
        count,
        max(0, startIndex - 1),
    )
    resources = [_render_user(r, request) for r in rows]
    return _list_response(resources, total=total, startIndex=startIndex, count=count)


@router.post("/Users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    body: dict[str, Any],
    ctx: SCIMContext = Depends(require_scim_token),
) -> dict[str, Any]:
    user_name = _required_str(body, "userName")
    external_id = body.get("externalId") or user_name
    full_name = _name_from_body(body)
    role = _role_from_body(body)
    active = body.get("active")
    active = True if active is None else bool(active)

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = ctx.workspace_id

    # Match-or-create the underlying app_user by email. A brand-new
    # email is created and attached. An email that already exists
    # globally may ONLY be attached if that account is already a member
    # of THIS workspace (invited via /members or previously provisioned
    # here).
    #
    # SECURITY (cross-tenant provisioning guard): without the membership
    # check, a workspace admin could mint a SCIM token for their own
    # workspace and POST /Users with any victim's email + role:"owner",
    # silently grafting that global identity into their tenant. Existing
    # accounts must be invited through the members API first.
    async with pool.acquire() as conn, conn.transaction():
        existing_user = await conn.fetchrow(
            "select id, email, deleted_at from app_user where lower(email) = lower($1)",
            user_name,
        )
        if existing_user is not None:
            if existing_user["deleted_at"] is not None:
                _scim_error(
                    409,
                    "User account is deactivated",
                    scim_type="uniqueness",
                )
            already_member = await conn.fetchval(
                "select 1 from workspace_member where workspace_id = $1 and user_id = $2",
                workspace_id,
                existing_user["id"],
            )
            if not already_member:
                _scim_error(
                    403,
                    "An account with this email already exists and is not a "
                    "member of this workspace. Invite it via the members API "
                    "before provisioning it through SCIM.",
                    scim_type="mutability",
                )
            user_id = existing_user["id"]
        else:
            user_id_row = await conn.fetchrow(
                """
                    insert into app_user (email, password_hash, full_name)
                    values ($1, NULL, $2)
                    returning id
                    """,
                user_name,
                full_name or user_name,
            )
            assert user_id_row is not None
            user_id = user_id_row["id"]

        # Reject duplicate provisioning into the same workspace.
        existing_map = await conn.fetchrow(
            """
                select id from scim_user_mapping
                 where workspace_id = $1
                   and (external_id = $2 or user_id = $3)
                """,
            workspace_id,
            external_id,
            user_id,
        )
        if existing_map is not None:
            _scim_error(
                409,
                "User already provisioned in this workspace",
                scim_type="uniqueness",
            )

        map_row = await conn.fetchrow(
            """
                insert into scim_user_mapping (
                    workspace_id, external_id, user_id, role, active
                )
                values ($1, $2, $3, $4, $5)
                returning id, external_id, workspace_id, role, active,
                          created_at, updated_at
                """,
            workspace_id,
            external_id,
            user_id,
            role,
            active,
        )
        assert map_row is not None

        if active:
            await conn.execute(
                """
                    insert into workspace_member (workspace_id, user_id, role)
                    values ($1, $2, $3)
                    on conflict (workspace_id, user_id)
                    do update set role = excluded.role
                    """,
                workspace_id,
                user_id,
                role,
            )

    await audit.record(
        pool,
        principal=None,
        action="scim.user.create",
        target_kind="scim_user_mapping",
        target_id=map_row["id"],
        payload={"external_id": external_id, "role": role, "active": active},
        request=request,
        workspace_id=workspace_id,
    )
    rendered = await _fetch_and_render(pool, ctx.workspace_id, map_row["id"], request)
    return rendered


@router.get("/Users/{mapping_id}")
async def get_user(
    request: Request,
    mapping_id: UUID,
    ctx: SCIMContext = Depends(require_scim_token),
) -> dict[str, Any]:
    pool: asyncpg.Pool = request.app.state.pg
    return await _fetch_and_render(pool, ctx.workspace_id, mapping_id, request)


@router.put("/Users/{mapping_id}")
async def replace_user(
    request: Request,
    mapping_id: UUID,
    body: dict[str, Any],
    ctx: SCIMContext = Depends(require_scim_token),
) -> dict[str, Any]:
    pool: asyncpg.Pool = request.app.state.pg
    full_name = _name_from_body(body)
    role = _role_from_body(body)
    active = body.get("active")
    active = True if active is None else bool(active)
    user_name = _required_str(body, "userName")

    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch_mapping_for_update(conn, ctx.workspace_id, mapping_id)
        # SECURITY: never let a workspace-scoped SCIM token rewrite the
        # globally-shared app_user.email — email is the identity key for
        # password login, OAuth and SSO resolution, so mutating it here
        # would let a workspace admin hijack or shadow any global account.
        # We only update the local display name; a differing userName is
        # rejected rather than silently applied.
        if user_name.lower() != (row["email"] or "").lower():
            _scim_error(
                400,
                "Changing a provisioned user's email is not supported.",
                scim_type="mutability",
            )
        await conn.execute(
            "update app_user set full_name = $2 where id = $1",
            row["user_id"],
            full_name or row["full_name"],
        )
        await conn.execute(
            """
                update scim_user_mapping
                   set role = $2,
                       active = $3
                 where id = $1
                """,
            mapping_id,
            role,
            active,
        )
        if active:
            await conn.execute(
                """
                    insert into workspace_member (workspace_id, user_id, role)
                    values ($1, $2, $3)
                    on conflict (workspace_id, user_id)
                    do update set role = excluded.role
                    """,
                ctx.workspace_id,
                row["user_id"],
                role,
            )
        else:
            await conn.execute(
                """
                    delete from workspace_member
                     where workspace_id = $1 and user_id = $2
                    """,
                ctx.workspace_id,
                row["user_id"],
            )

    await audit.record(
        pool,
        principal=None,
        action="scim.user.replace",
        target_kind="scim_user_mapping",
        target_id=mapping_id,
        payload={"role": role, "active": active},
        request=request,
        workspace_id=ctx.workspace_id,
    )
    return await _fetch_and_render(pool, ctx.workspace_id, mapping_id, request)


@router.patch("/Users/{mapping_id}")
async def patch_user(
    request: Request,
    mapping_id: UUID,
    body: dict[str, Any],
    ctx: SCIMContext = Depends(require_scim_token),
) -> dict[str, Any]:
    operations = body.get("Operations") or body.get("operations") or []
    if not isinstance(operations, list):
        _scim_error(400, "Operations must be a list")

    pool: asyncpg.Pool = request.app.state.pg
    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch_mapping_for_update(conn, ctx.workspace_id, mapping_id)
        user_id: UUID = row["user_id"]
        new_role: str = row["role"]
        new_active: bool = row["active"]
        new_email: str = row["email"]
        new_full_name: str | None = row["full_name"]

        for raw_op in operations:
            if not isinstance(raw_op, dict):
                continue
            op = str(raw_op.get("op") or "").lower()
            path = str(raw_op.get("path") or "").strip()
            value = raw_op.get("value")
            if op not in {"add", "replace", "remove"}:
                _scim_error(400, f"Unsupported op: {op}")
            if path == "" and op == "replace" and isinstance(value, dict):
                if "userName" in value:
                    new_email = str(value["userName"])
                if "active" in value:
                    new_active = bool(value["active"])
                if "displayName" in value:
                    new_full_name = str(value["displayName"])
                if "name" in value and isinstance(value["name"], dict):
                    f = value["name"].get("formatted")
                    if isinstance(f, str):
                        new_full_name = f
                enterprise = value.get("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User")
                if isinstance(enterprise, dict):
                    new_role = _coerce_role(enterprise.get("role")) or new_role
                continue
            if path == "active":
                if op == "remove":
                    new_active = False
                else:
                    new_active = bool(value)
            elif path == "userName":
                if op == "remove":
                    _scim_error(400, "Cannot remove userName")
                new_email = str(value)
            elif path == "displayName" or path == "name.formatted":
                if op == "remove":
                    new_full_name = None
                else:
                    new_full_name = str(value)
            elif path.startswith("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:role"):
                if op == "remove":
                    new_role = "member"
                else:
                    new_role = _coerce_role(value) or new_role
            elif path == "emails":
                if isinstance(value, list) and value:
                    first = value[0] if isinstance(value[0], dict) else {}
                    v = first.get("value")
                    if isinstance(v, str):
                        new_email = v

        # SECURITY: as with PUT, a workspace-scoped SCIM token must not
        # rewrite the globally-shared app_user.email. Reject a changed
        # userName/email rather than mutating the shared identity row;
        # only the local display name is updated here.
        if new_email.lower() != (row["email"] or "").lower():
            _scim_error(
                400,
                "Changing a provisioned user's email is not supported.",
                scim_type="mutability",
            )
        await conn.execute(
            "update app_user set full_name = $2 where id = $1",
            user_id,
            new_full_name,
        )
        await conn.execute(
            """
                update scim_user_mapping
                   set role = $2, active = $3
                 where id = $1
                """,
            mapping_id,
            new_role,
            new_active,
        )
        if new_active:
            await conn.execute(
                """
                    insert into workspace_member (workspace_id, user_id, role)
                    values ($1, $2, $3)
                    on conflict (workspace_id, user_id)
                    do update set role = excluded.role
                    """,
                ctx.workspace_id,
                user_id,
                new_role,
            )
        else:
            await conn.execute(
                """
                    delete from workspace_member
                     where workspace_id = $1 and user_id = $2
                    """,
                ctx.workspace_id,
                user_id,
            )

    await audit.record(
        pool,
        principal=None,
        action="scim.user.patch",
        target_kind="scim_user_mapping",
        target_id=mapping_id,
        payload={"ops": len(operations)},
        request=request,
        workspace_id=ctx.workspace_id,
    )
    return await _fetch_and_render(pool, ctx.workspace_id, mapping_id, request)


@router.delete("/Users/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    mapping_id: UUID,
    ctx: SCIMContext = Depends(require_scim_token),
) -> Response:
    """SCIM DELETE → deactivate (soft-delete).

    SCIM RFC says DELETE removes the resource; in practice IdPs
    expect deactivation so the mapping survives a re-activation
    request. We delete the workspace_member row but keep the SCIM
    mapping marked inactive.
    """
    pool: asyncpg.Pool = request.app.state.pg
    async with pool.acquire() as conn, conn.transaction():
        row = await _fetch_mapping_for_update(conn, ctx.workspace_id, mapping_id)
        await conn.execute(
            "update scim_user_mapping set active = false where id = $1",
            mapping_id,
        )
        await conn.execute(
            """
                delete from workspace_member
                 where workspace_id = $1 and user_id = $2
                """,
            ctx.workspace_id,
            row["user_id"],
        )
    await audit.record(
        pool,
        principal=None,
        action="scim.user.delete",
        target_kind="scim_user_mapping",
        target_id=mapping_id,
        payload={},
        request=request,
        workspace_id=ctx.workspace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required_str(body: dict[str, Any], key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        _scim_error(400, f"Missing required field '{key}'", scim_type="invalidValue")
    return v.strip()


def _name_from_body(body: dict[str, Any]) -> str:
    name = body.get("name")
    if isinstance(name, dict):
        formatted = name.get("formatted")
        if isinstance(formatted, str) and formatted.strip():
            return formatted.strip()
        given = str(name.get("givenName") or "").strip()
        family = str(name.get("familyName") or "").strip()
        joined = (given + " " + family).strip()
        if joined:
            return joined
    display = body.get("displayName")
    if isinstance(display, str) and display.strip():
        return display.strip()
    return ""


def _role_from_body(body: dict[str, Any]) -> str:
    enterprise = body.get("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User")
    if isinstance(enterprise, dict):
        role = _coerce_role(enterprise.get("role"))
        if role:
            return role
    return "member"


def _coerce_role(value: Any) -> str | None:
    if isinstance(value, str) and value.lower() in {"owner", "admin", "member", "viewer"}:
        return value.lower()
    return None


async def _fetch_mapping_for_update(
    conn: asyncpg.Connection, workspace_id: UUID, mapping_id: UUID
) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        select m.id, m.external_id, m.user_id, m.role, m.active, m.created_at,
               m.updated_at, u.email, u.full_name
          from scim_user_mapping m
          join app_user u on u.id = m.user_id
         where m.id = $1 and m.workspace_id = $2
         for update of m
        """,
        mapping_id,
        workspace_id,
    )
    if row is None:
        _scim_error(404, "User not found")
    return row


async def _fetch_and_render(
    pool: asyncpg.Pool, workspace_id: UUID, mapping_id: UUID, request: Request
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        select m.id, m.external_id, m.workspace_id, m.role, m.active,
               m.created_at, m.updated_at,
               u.id as user_id, u.email, u.full_name
          from scim_user_mapping m
          join app_user u on u.id = m.user_id
         where m.id = $1 and m.workspace_id = $2
        """,
        mapping_id,
        workspace_id,
    )
    if row is None:
        _scim_error(404, "User not found")
    return _render_user(row, request)


def _render_user(row: asyncpg.Record, request: Request) -> dict[str, Any]:
    base = f"{request.url.scheme}://{request.url.netloc}/scim/v2/Users/{row['id']}"
    return {
        "schemas": [
            "urn:ietf:params:scim:schemas:core:2.0:User",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
        ],
        "id": str(row["id"]),
        "externalId": row["external_id"],
        "userName": row["email"],
        "displayName": row.get("full_name") or row["email"],
        "name": {
            "formatted": row.get("full_name") or row["email"],
        },
        "active": bool(row["active"]),
        "emails": [{"value": row["email"], "primary": True}],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
            "role": row["role"],
        },
        "meta": {
            "resourceType": "User",
            "created": _iso(row["created_at"]),
            "lastModified": _iso(row["updated_at"]),
            "location": base,
        },
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _list_response(
    resources: list[dict[str, Any]],
    *,
    total: int | None = None,
    startIndex: int = 1,
    count: int | None = None,
) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total if total is not None else len(resources),
        "startIndex": startIndex,
        "itemsPerPage": count if count is not None else len(resources),
        "Resources": resources,
    }


# ---------------------------------------------------------------------------
# Static SCIM schemas (returned by /Schemas)
# ---------------------------------------------------------------------------


_USER_SCHEMA: dict[str, Any] = {
    "id": "urn:ietf:params:scim:schemas:core:2.0:User",
    "name": "User",
    "description": "langprobe User",
    "attributes": [
        {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
        {"name": "displayName", "type": "string"},
        {"name": "active", "type": "boolean"},
        {
            "name": "name",
            "type": "complex",
            "subAttributes": [
                {"name": "formatted", "type": "string"},
                {"name": "givenName", "type": "string"},
                {"name": "familyName", "type": "string"},
            ],
        },
        {
            "name": "emails",
            "type": "complex",
            "multiValued": True,
            "subAttributes": [
                {"name": "value", "type": "string"},
                {"name": "primary", "type": "boolean"},
            ],
        },
    ],
}

_ENTERPRISE_USER_SCHEMA: dict[str, Any] = {
    "id": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
    "name": "EnterpriseUser",
    "description": "Enterprise User extension (role mapping)",
    "attributes": [
        {
            "name": "role",
            "type": "string",
            "canonicalValues": ["owner", "admin", "member", "viewer"],
        }
    ],
}
