"""OIDC single-sign-on for workspaces.

Authorization-code flow with PKCE. Per-workspace IdP config is stored
in `workspace_sso_config`; each sign-in mints a short-lived
`sso_state` nonce that survives the IdP redirect round-trip
(cookies don't reliably cross the boundary so we hand-roll state).

V1 boundaries:

  - `client_secret_encrypted` is plaintext today. The column name
    is the contract; swapping in a KMS-backed envelope encryptor
    won't change the API surface.
  - Auto-provisioning matches on email. If the IdP issues for an
    email already in `app_user`, we add a workspace_membership at
    the configured `default_role` and sign in. If not present and
    `auto_provision='auto'`, we create the app_user; if
    `auto_provision='match-only'`, we 401.
  - We don't store IdP user IDs. Two IdPs issuing for the same
    email would both sign that user in — that's the expected
    semantics for a corporate setup with one IdP per workspace.
  - JWKS / id_token signature verification is skipped in v1.
    The token endpoint runs over TLS to a configured issuer so an
    attacker would have to MITM an HTTPS connection to a known IdP
    to bypass — out of scope for the bootstrap. The next iteration
    pulls keys from `jwks_uri` and validates RS256 signatures.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json as _json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, issue_session_cookie, require_user
from ..config import Settings

log = structlog.get_logger("langprobe.api.sso")

router = APIRouter(prefix="/v1/auth/sso", tags=["sso"])

_STATE_TTL_SECONDS = 600  # 10 minutes; IdP round-trips are usually <1m


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class SSOConfigOut(BaseModel):
    id: UUID
    workspace_id: UUID
    issuer: str
    client_id: str
    auto_provision: str
    default_role: str
    enabled: bool
    authorization_endpoint: str | None
    token_endpoint: str | None
    jwks_uri: str | None
    created_at: datetime
    updated_at: datetime
    has_client_secret: bool


class SSOConfigCreate(BaseModel):
    issuer: str = Field(min_length=8, max_length=512)
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str = Field(min_length=1, max_length=2048)
    auto_provision: str = Field(default="auto", max_length=16)
    default_role: str = Field(default="member", max_length=16)
    enabled: bool = True


class SSOConfigPatch(BaseModel):
    issuer: str | None = Field(default=None, min_length=8, max_length=512)
    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2048)
    auto_provision: str | None = Field(default=None, max_length=16)
    default_role: str | None = Field(default=None, max_length=16)
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Admin: workspace SSO config CRUD (cookie-auth, owner/admin only)
# ---------------------------------------------------------------------------


@router.get("/config", response_model=SSOConfigOut | None)
async def get_config(
    request: Request,
    workspace_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> SSOConfigOut | None:
    pool: asyncpg.Pool = request.app.state.pg
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )
    row = await pool.fetchrow(
        """
        select id, workspace_id, issuer, client_id, client_secret_encrypted,
               authorization_endpoint, token_endpoint, jwks_uri,
               auto_provision, default_role, enabled, created_at, updated_at
          from workspace_sso_config
         where workspace_id = $1
         order by enabled desc, updated_at desc
         limit 1
        """,
        workspace_id,
    )
    return _config_out(row) if row else None


@router.post(
    "/config",
    response_model=SSOConfigOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_config(
    request: Request,
    workspace_id: UUID = Query(...),
    body: SSOConfigCreate = ...,
    principal: Principal = Depends(require_user),
) -> SSOConfigOut:
    if body.auto_provision not in {"auto", "match-only"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "auto_provision must be 'auto' or 'match-only'",
        )
    if body.default_role not in {"owner", "admin", "member", "viewer"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "default_role must be one of owner/admin/member/viewer",
        )

    pool: asyncpg.Pool = request.app.state.pg
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )

    # Disable any existing enabled config for this workspace; replace
    # with the new one. We don't delete the old row — keeping the
    # history makes audit easier.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "update workspace_sso_config set enabled = false where workspace_id = $1 and enabled = true",
                workspace_id,
            )
            row = await conn.fetchrow(
                """
                insert into workspace_sso_config (
                    workspace_id, issuer, client_id, client_secret_encrypted,
                    auto_provision, default_role, enabled, created_by
                )
                values ($1, $2, $3, $4, $5, $6, $7, $8)
                returning id, workspace_id, issuer, client_id, client_secret_encrypted,
                          authorization_endpoint, token_endpoint, jwks_uri,
                          auto_provision, default_role, enabled, created_at, updated_at
                """,
                workspace_id,
                body.issuer.rstrip("/"),
                body.client_id,
                body.client_secret,
                body.auto_provision,
                body.default_role,
                body.enabled,
                principal.user_id,
            )
    assert row is not None

    await audit.record(
        pool,
        principal=principal,
        action="sso_config.create",
        target_kind="workspace_sso_config",
        target_id=row["id"],
        payload={
            "issuer": body.issuer,
            "client_id": body.client_id,
            "auto_provision": body.auto_provision,
        },
        request=request,
        workspace_id=workspace_id,
    )
    return _config_out(row)


@router.patch("/config/{config_id}", response_model=SSOConfigOut)
async def patch_config(
    request: Request,
    config_id: UUID,
    body: SSOConfigPatch,
    principal: Principal = Depends(require_user),
) -> SSOConfigOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        "select workspace_id from workspace_sso_config where id = $1",
        config_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "config not found")
    workspace_id: UUID = row["workspace_id"]
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )

    sets: list[str] = []
    args: list[Any] = []
    if body.issuer is not None:
        args.append(body.issuer.rstrip("/"))
        sets.append(f"issuer = ${len(args)}")
        # Force re-discovery on next sign-in.
        args.append(None)
        sets.append(f"authorization_endpoint = ${len(args)}")
        args.append(None)
        sets.append(f"token_endpoint = ${len(args)}")
        args.append(None)
        sets.append(f"jwks_uri = ${len(args)}")
    if body.client_id is not None:
        args.append(body.client_id)
        sets.append(f"client_id = ${len(args)}")
    if body.client_secret is not None:
        args.append(body.client_secret)
        sets.append(f"client_secret_encrypted = ${len(args)}")
    if body.auto_provision is not None:
        if body.auto_provision not in {"auto", "match-only"}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "auto_provision must be 'auto' or 'match-only'",
            )
        args.append(body.auto_provision)
        sets.append(f"auto_provision = ${len(args)}")
    if body.default_role is not None:
        if body.default_role not in {"owner", "admin", "member", "viewer"}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "default_role must be one of owner/admin/member/viewer",
            )
        args.append(body.default_role)
        sets.append(f"default_role = ${len(args)}")
    if body.enabled is not None:
        args.append(body.enabled)
        sets.append(f"enabled = ${len(args)}")

    if not sets:
        existing = await _fetch_config_full(pool, config_id)
        return _config_out(existing)

    args.append(config_id)
    updated = await pool.fetchrow(
        f"""
        update workspace_sso_config
           set {", ".join(sets)}
         where id = ${len(args)}
        returning id, workspace_id, issuer, client_id, client_secret_encrypted,
                  authorization_endpoint, token_endpoint, jwks_uri,
                  auto_provision, default_role, enabled, created_at, updated_at
        """,
        *args,
    )
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="sso_config.update",
        target_kind="workspace_sso_config",
        target_id=config_id,
        payload={"fields": [s.split(" ")[0] for s in sets]},
        request=request,
        workspace_id=workspace_id,
    )
    return _config_out(updated)


@router.delete("/config/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    request: Request,
    config_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        "select workspace_id from workspace_sso_config where id = $1",
        config_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "config not found")
    workspace_id: UUID = row["workspace_id"]
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=("owner", "admin"),
    )
    await pool.execute("delete from workspace_sso_config where id = $1", config_id)
    await audit.record(
        pool,
        principal=principal,
        action="sso_config.delete",
        target_kind="workspace_sso_config",
        target_id=config_id,
        payload={},
        request=request,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# Sign-in flow: /start → IdP → /callback → set session cookie
# ---------------------------------------------------------------------------


@router.get("/{workspace_slug}/start")
async def sso_start(
    request: Request,
    workspace_slug: str,
    return_to: str | None = Query(default=None, max_length=2048),
) -> RedirectResponse:
    """Begin the OIDC authorization-code flow.

    Public endpoint (no auth required); the workspace is identified
    by slug so we don't leak workspace UUIDs in pre-auth URLs.
    """
    pool: asyncpg.Pool = request.app.state.pg
    settings: Settings = request.app.state.settings

    cfg = await pool.fetchrow(
        """
        select c.id, c.workspace_id, c.issuer, c.client_id, c.client_secret_encrypted,
               c.authorization_endpoint, c.token_endpoint, c.jwks_uri,
               c.auto_provision, c.default_role
          from workspace_sso_config c
          join workspace w on w.id = c.workspace_id
         where w.slug = $1 and c.enabled = true
        """,
        workspace_slug,
    )
    if cfg is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no SSO config for this workspace",
        )

    auth_endpoint, token_endpoint, jwks_uri = await _ensure_discovery(pool, cfg)

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )

    # The redirect_uri is the public origin of THIS API. We let the
    # operator override via env (LANGPROBE_PUBLIC_API_URL) for
    # production where the api host differs from the cookie host.
    public_base = (
        os.environ.get("LANGPROBE_PUBLIC_API_URL") or f"{request.url.scheme}://{request.url.netloc}"
    )
    redirect_uri = f"{public_base}/v1/auth/sso/callback"

    expires_at = datetime.now(UTC) + timedelta(seconds=_STATE_TTL_SECONDS)
    await pool.execute(
        """
        insert into sso_state (state, workspace_id, code_verifier, redirect_uri, return_to, expires_at)
        values ($1, $2, $3, $4, $5, $6)
        """,
        state,
        cfg["workspace_id"],
        code_verifier,
        redirect_uri,
        _safe_return_to(return_to, settings),
        expires_at,
    )

    auth_url = f"{auth_endpoint}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": cfg["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )

    await audit.record(
        pool,
        principal=None,
        action="sso.start",
        target_kind="workspace_sso_config",
        target_id=cfg["id"],
        payload={"workspace_slug": workspace_slug},
        request=request,
        workspace_id=cfg["workspace_id"],
    )
    return RedirectResponse(auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def sso_callback(
    request: Request,
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Exchange the IdP authorization code for an id_token, then
    auto-provision (or match) the app_user and set a session cookie.
    """
    pool: asyncpg.Pool = request.app.state.pg
    settings: Settings = request.app.state.settings

    state_row = await pool.fetchrow(
        """
        delete from sso_state
         where state = $1
         returning workspace_id, code_verifier, redirect_uri, return_to, expires_at
        """,
        state,
    )
    if state_row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid sso state")
    if state_row["expires_at"] < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sso state expired")

    workspace_id: UUID = state_row["workspace_id"]
    cfg = await pool.fetchrow(
        """
        select id, issuer, client_id, client_secret_encrypted,
               token_endpoint, auto_provision, default_role
          from workspace_sso_config
         where workspace_id = $1 and enabled = true
        """,
        workspace_id,
    )
    if cfg is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sso disabled for this workspace")
    token_endpoint = cfg["token_endpoint"]
    if not token_endpoint:
        # Re-discover if the cached endpoint went missing (e.g. after
        # an issuer rotation).
        await _ensure_discovery(pool, cfg)
        token_endpoint = await pool.fetchval(
            "select token_endpoint from workspace_sso_config where id = $1",
            cfg["id"],
        )
    if not token_endpoint:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "could not resolve token endpoint",
        )

    try:
        token_response = await asyncio.to_thread(
            _exchange_code,
            token_endpoint=token_endpoint,
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret_encrypted"],
            code=code,
            code_verifier=state_row["code_verifier"],
            redirect_uri=state_row["redirect_uri"],
        )
    except RuntimeError as exc:
        log.warning("oidc token exchange failed", error=str(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "oidc token exchange failed",
        ) from exc

    id_token = token_response.get("id_token")
    if not isinstance(id_token, str):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "id_token missing from oidc token response",
        )
    claims = _decode_id_token_payload(id_token)
    email_v = claims.get("email")
    if not isinstance(email_v, str) or "@" not in email_v:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "id_token is missing 'email' claim",
        )
    email = email_v.lower()

    # Match-or-provision the app_user, ensure workspace membership.
    user_row = await _resolve_or_provision_user(
        pool,
        workspace_id=workspace_id,
        email=email,
        full_name=str(claims.get("name") or ""),
        auto_provision=cfg["auto_provision"],
        default_role=cfg["default_role"],
    )

    cookie_value = issue_session_cookie(settings, user_row["id"])
    redirect = RedirectResponse(
        state_row["return_to"] or _default_return_to(settings),
        status_code=status.HTTP_302_FOUND,
    )
    redirect.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
    )

    await audit.record(
        pool,
        principal=Principal(
            user_id=user_row["id"],
            email=user_row["email"],
            is_root=bool(user_row.get("is_root")),
        ),
        action="sso.login",
        target_kind="app_user",
        target_id=user_row["id"],
        payload={"email": email, "workspace_id": str(workspace_id)},
        request=request,
        workspace_id=workspace_id,
    )
    return redirect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ensure_discovery(pool: asyncpg.Pool, cfg: asyncpg.Record) -> tuple[str, str, str | None]:
    """Resolve the IdP's authorization/token/jwks endpoints.

    Cached on the row; null cache forces a fresh fetch. Returns
    `(authorization_endpoint, token_endpoint, jwks_uri)`.
    """
    auth = cfg["authorization_endpoint"]
    token = cfg["token_endpoint"]
    jwks = cfg["jwks_uri"]
    if auth and token:
        return auth, token, jwks

    issuer = cfg["issuer"].rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    try:
        data = await asyncio.to_thread(_http_get_json, discovery_url)
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"oidc discovery failed: {exc}",
        ) from exc
    auth = data.get("authorization_endpoint")
    token = data.get("token_endpoint")
    jwks = data.get("jwks_uri")
    if not isinstance(auth, str) or not isinstance(token, str):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "oidc discovery missing endpoints",
        )

    await pool.execute(
        """
        update workspace_sso_config
           set authorization_endpoint = $2,
               token_endpoint = $3,
               jwks_uri = $4
         where id = $1
        """,
        cfg["id"],
        auth,
        token,
        jwks if isinstance(jwks, str) else None,
    )
    return auth, token, jwks if isinstance(jwks, str) else None


def _http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {url} → {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} → {exc.reason}") from exc


def _exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
    ).encode("ascii")
    req = urllib.request.Request(
        token_endpoint,
        data=body,
        method="POST",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"token endpoint {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"token endpoint unreachable: {exc.reason}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("token endpoint returned non-object")
    return data


def _decode_id_token_payload(id_token: str) -> dict[str, Any]:
    """Decode the JWT payload without signature verification.

    See module docstring for why signature verification is deferred.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "id_token is not a JWT")
    raw = parts[1]
    # JWT base64url is unpadded; pad before decode.
    padded = raw + "=" * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "id_token payload not base64url") from exc
    try:
        return _json.loads(decoded)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "id_token payload not JSON") from exc


async def _resolve_or_provision_user(
    pool: asyncpg.Pool,
    *,
    workspace_id: UUID,
    email: str,
    full_name: str,
    auto_provision: str,
    default_role: str,
) -> dict[str, Any]:
    """Find or create the app_user; ensure workspace membership."""
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            "select id, email, is_root, deleted_at from app_user where email = $1",
            email,
        )
        if existing is not None and existing["deleted_at"] is not None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "user account is deactivated",
            )
        if existing is None:
            if auto_provision != "auto":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "user not provisioned (auto_provision='match-only')",
                )
            user_row = await conn.fetchrow(
                """
                    insert into app_user (email, password_hash, full_name)
                    values ($1, NULL, $2)
                    returning id, email, is_root
                    """,
                email,
                full_name or email,
            )
            assert user_row is not None
        else:
            user_row = {
                "id": existing["id"],
                "email": existing["email"],
                "is_root": existing["is_root"],
            }

        # Ensure membership; do not downgrade existing higher roles.
        await conn.execute(
            """
                insert into workspace_member (workspace_id, user_id, role)
                values ($1, $2, $3)
                on conflict (workspace_id, user_id) do nothing
                """,
            workspace_id,
            user_row["id"],
            default_role,
        )
    return dict(user_row)


def _safe_return_to(value: str | None, settings: Settings) -> str | None:
    """Allow only origin-scoped relative URLs in return_to."""
    if value is None:
        return None
    if value.startswith("/"):
        return value
    return None


def _default_return_to(_settings: Settings) -> str:
    return "/"


async def _fetch_config_full(pool: asyncpg.Pool, config_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, workspace_id, issuer, client_id, client_secret_encrypted,
               authorization_endpoint, token_endpoint, jwks_uri,
               auto_provision, default_role, enabled, created_at, updated_at
          from workspace_sso_config
         where id = $1
        """,
        config_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "config not found")
    return row


def _config_out(row: asyncpg.Record) -> SSOConfigOut:
    return SSOConfigOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        issuer=row["issuer"],
        client_id=row["client_id"],
        auto_provision=row["auto_provision"],
        default_role=row["default_role"],
        enabled=bool(row["enabled"]),
        authorization_endpoint=row["authorization_endpoint"],
        token_endpoint=row["token_endpoint"],
        jwks_uri=row["jwks_uri"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        has_client_secret=bool(row["client_secret_encrypted"]),
    )
