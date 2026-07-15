"""Playground — interactive prompt + model invocations.

A playground session is "render this prompt with these variables,
call this model, write the result everywhere a trace would land".
Concretely, the request handler:

1. Persists a `playground_session` row in postgres (status=running).
2. Renders each message's content (Jinja-style ``{{ var }}`` substitution)
   and persists both the structured rendered_messages and a newline-
   joined rendered_prompt for the trace UI's display path.
3. Calls the chosen LLM provider over HTTP.
4. Writes a `run` + `span` to ClickHouse with `sdk='playground'` so
   the result is visible at `/runs/{id}` like any other trace.
5. Flips the session row to status=done (or failed with error text)
   and returns the row.

Provider selection is derived from the model string:
  - `claude-*` → anthropic
  - `gpt-*` / `o*` → openai
  - `stub-*` → deterministic echo for tests + the no-key smoke path

Credentials come from env (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`).
We do NOT yet store per-workspace keys in postgres -- the next
iteration adds an encrypted `workspace_llm_credential` table; for
v1 the env path is fine for self-hosted single-tenant.

ER-23: never silent-drop. A provider 5xx writes status='failed' with
the error text rather than discarding the attempt. The user sees
exactly what went wrong.
"""

from __future__ import annotations

import json as _json
import re
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..llm import Message as DispatchMessage
from ..tenant_scope import resolve_tenant_ids
from .prompts import Message

log = structlog.get_logger("langprobe.api.playground")

router = APIRouter(prefix="/v1/playground", tags=["playground"])

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_PROVIDERS = {"anthropic", "openai", "gemini", "mistral", "deepseek", "groq", "stub"}
_DEFAULT_MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class PlaygroundCreate(BaseModel):
    project_id: UUID
    prompt_version_id: UUID | None = None
    # Legacy single-string template. Wraps to a single human message
    # at render time. Drops in the cleanup PR after Plan C lands.
    raw_template: str | None = None
    # Structured form added in Plan B. Mutually exclusive with
    # raw_template and prompt_version_id (xor enforced below).
    # min_length=1 rejects an empty list at field-validation time with a
    # structured `type=too_short` error scoped to `loc=('raw_messages',)`,
    # before the model validator runs.
    raw_messages: list[Message] | None = Field(default=None, min_length=1)
    variables: dict[str, Any] = Field(default_factory=dict)
    model: str = Field(min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)

    @model_validator(mode="after")
    def _exactly_one_template_source(self) -> PlaygroundCreate:
        sources = [
            self.prompt_version_id is not None,
            self.raw_template is not None,
            self.raw_messages is not None,
        ]
        n = sum(sources)
        if n == 0:
            raise ValueError("one of prompt_version_id, raw_template, raw_messages is required")
        if n > 1:
            raise ValueError(
                "prompt_version_id, raw_template, raw_messages are mutually "
                "exclusive; provide exactly one"
            )
        return self


class PlaygroundSessionOut(BaseModel):
    id: UUID
    project_id: UUID
    prompt_version_id: UUID | None
    raw_template: str | None
    rendered_prompt: str
    variables: dict[str, Any]
    provider: str
    model: str
    temperature: float | None
    max_tokens: int | None
    status: str
    output_text: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    run_id: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class PlaygroundSessionList(BaseModel):
    items: list[PlaygroundSessionOut]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=PlaygroundSessionList)
async def list_sessions(
    request: Request,
    project_id: UUID = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_user),
) -> PlaygroundSessionList:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, prompt_version_id, raw_template,
               rendered_prompt, variables, provider, model, temperature,
               max_tokens, status, output_text, prompt_tokens,
               completion_tokens, total_tokens, cost_usd, latency_ms,
               run_id, error, created_at, finished_at
          from playground_session
         where project_id = $1
         order by created_at desc
         limit $2
        """,
        project_id,
        limit,
    )
    return PlaygroundSessionList(items=[_session_out(r) for r in rows])


@router.post(
    "/runs",
    response_model=PlaygroundSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    request: Request,
    body: PlaygroundCreate,
    principal: Principal = Depends(require_user),
) -> PlaygroundSessionOut:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    provider = _resolve_provider(body.model)

    # Resolve the message list to render. raw_messages > prompt_version_id
    # > raw_template per the xor validator on PlaygroundCreate.
    template_messages, version_row = await _resolve_messages(pool, body)

    rendered_messages = _render_messages(template_messages, body.variables)
    # Newline-joined readable view for the trace UI's existing display
    # path. The structured rendered_messages is what replay reads.
    rendered_prompt = "\n\n".join(m.content for m in rendered_messages)

    started = time.monotonic()
    session_row = await pool.fetchrow(
        """
        insert into playground_session (
            project_id, prompt_version_id, raw_template, raw_messages,
            rendered_prompt, rendered_messages, variables, provider, model,
            temperature, max_tokens, status, created_by
        )
        values ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, 'running', $12)
        returning id, project_id, prompt_version_id, raw_template,
                  rendered_prompt, variables, provider, model, temperature,
                  max_tokens, status, output_text, prompt_tokens,
                  completion_tokens, total_tokens, cost_usd, latency_ms,
                  run_id, error, created_at, finished_at
        """,
        body.project_id,
        body.prompt_version_id,
        # raw_template column on the session is only persisted for the
        # legacy single-string path. raw_messages and prompt_version_id
        # paths leave it null so the trace UI doesn't show a misleading
        # legacy snippet.
        None if (version_row is not None or body.raw_messages is not None) else body.raw_template,
        _json.dumps([m.model_dump() for m in body.raw_messages]) if body.raw_messages else None,
        rendered_prompt,
        _json.dumps([m.model_dump() for m in rendered_messages]),
        _json.dumps(body.variables),
        provider,
        body.model,
        body.temperature,
        body.max_tokens or _DEFAULT_MAX_TOKENS,
        principal.user_id,
    )
    assert session_row is not None
    session_id: UUID = session_row["id"]

    await audit.record(
        pool,
        principal=principal,
        action="playground.run",
        target_kind="playground_session",
        target_id=session_id,
        payload={
            "model": body.model,
            "provider": provider,
            "prompt_version_id": str(body.prompt_version_id) if body.prompt_version_id else None,
            "raw_template": body.raw_template is not None,
            "raw_messages": body.raw_messages is not None,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )

    # Execute the LLM call via the LiteLLM gateway. Cost + tokens are
    # recorded in dispatch_cost by the gateway; the session row only
    # holds the surface state.
    if provider == "stub":
        result_dict = await _dispatch_stub(body.model, rendered_prompt)
    else:
        from ..llm import DispatchError
        from ..llm import dispatch as gateway_dispatch

        try:
            gw = await gateway_dispatch(
                pool,
                project_id=body.project_id,
                surface="playground",
                surface_ref_id=session_id,
                model=f"{provider}/{body.model.removeprefix(provider + '/')}",
                messages=_to_dispatch_messages(rendered_messages),
                temperature=body.temperature,
                max_tokens=body.max_tokens or _DEFAULT_MAX_TOKENS,
            )
            result_dict = {
                "text": gw.text,
                "prompt_tokens": gw.prompt_tokens,
                "completion_tokens": gw.completion_tokens,
            }
        except DispatchError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            failed = await pool.fetchrow(
                """
                update playground_session
                   set status = 'failed',
                       error = $2,
                       latency_ms = $3,
                       finished_at = $4
                 where id = $1
                returning id, project_id, prompt_version_id, raw_template,
                          rendered_prompt, variables, provider, model, temperature,
                          max_tokens, status, output_text, prompt_tokens,
                          completion_tokens, total_tokens, cost_usd, latency_ms,
                          run_id, error, created_at, finished_at
                """,
                session_id,
                f"[{exc.code}] {exc.detail}",
                latency_ms,
                datetime.now(UTC),
            )
            assert failed is not None
            return _session_out(failed)

    try:
        result = result_dict
        latency_ms = int((time.monotonic() - started) * 1000)
        run_id = str(uuid.uuid4())
        org_id, ws_id = await resolve_tenant_ids(pool, body.project_id)
        await _write_clickhouse_trace(
            request.app.state.clickhouse,
            org_id=org_id,
            workspace_id=ws_id,
            project_id=body.project_id,
            run_id=run_id,
            model=body.model,
            temperature=body.temperature,
            prompt=rendered_prompt,
            output=result["text"],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
            session_id=str(session_id),
            user_id=str(principal.user_id),
        )
        updated = await pool.fetchrow(
            """
            update playground_session
               set status = 'done',
                   output_text = $2,
                   prompt_tokens = $3,
                   completion_tokens = $4,
                   total_tokens = $5,
                   latency_ms = $6,
                   run_id = $7,
                   finished_at = $8
             where id = $1
            returning id, project_id, prompt_version_id, raw_template,
                      rendered_prompt, variables, provider, model, temperature,
                      max_tokens, status, output_text, prompt_tokens,
                      completion_tokens, total_tokens, cost_usd, latency_ms,
                      run_id, error, created_at, finished_at
            """,
            session_id,
            result["text"],
            result["prompt_tokens"],
            result["completion_tokens"],
            (result["prompt_tokens"] or 0) + (result["completion_tokens"] or 0),
            latency_ms,
            run_id,
            datetime.now(UTC),
        )
        assert updated is not None
        return _session_out(updated)
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - started) * 1000)
        log.warning(
            "playground execution failed",
            session_id=str(session_id),
            provider=provider,
            error=str(exc),
        )
        failed = await pool.fetchrow(
            """
            update playground_session
               set status = 'failed',
                   error = $2,
                   latency_ms = $3,
                   finished_at = $4
             where id = $1
            returning id, project_id, prompt_version_id, raw_template,
                      rendered_prompt, variables, provider, model, temperature,
                      max_tokens, status, output_text, prompt_tokens,
                      completion_tokens, total_tokens, cost_usd, latency_ms,
                      run_id, error, created_at, finished_at
            """,
            session_id,
            f"{type(exc).__name__}: {exc}"[:2000],
            latency_ms,
            datetime.now(UTC),
        )
        assert failed is not None
        return _session_out(failed)


@router.get("/runs/{session_id}", response_model=PlaygroundSessionOut)
async def get_session(
    request: Request,
    session_id: UUID,
    principal: Principal = Depends(require_user),
) -> PlaygroundSessionOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await pool.fetchrow(
        """
        select id, project_id, prompt_version_id, raw_template,
               rendered_prompt, variables, provider, model, temperature,
               max_tokens, status, output_text, prompt_tokens,
               completion_tokens, total_tokens, cost_usd, latency_ms,
               run_id, error, created_at, finished_at
          from playground_session where id = $1
        """,
        session_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    return _session_out(row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_provider(model: str) -> str:
    name = model.lower()
    if name.startswith("stub-") or name.startswith("stub/"):
        return "stub"
    if name.startswith("claude-") or name.startswith("anthropic/"):
        return "anthropic"
    if (
        name.startswith("gpt-")
        or name.startswith("o1-")
        or name.startswith("o3-")
        or name.startswith("o4-")
        or name.startswith("openai/")
    ):
        return "openai"
    for provider in ("gemini", "mistral", "deepseek", "groq"):
        if name.startswith(provider + "/") or name.startswith(provider + "-"):
            return provider
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"unknown provider for model '{model}' (expected a "
        "'<provider>/<model>' id or claude-*, gpt-*, o*-*, stub-*)",
    )


async def _resolve_messages(
    pool: asyncpg.Pool,
    body: PlaygroundCreate,
) -> tuple[list[Message], asyncpg.Record | None]:
    """Return the message list to render + the version row (if any).

    Resolution order matches PlaygroundCreate's xor validator:
      1. raw_messages   - explicit; use as-is.
      2. prompt_version_id - read template_messages from prompt_version.
      3. raw_template   - wrap as [{role: human, content: <s>}].

    Exactly one of these is set per the model validator; the validator
    runs before this function so the assertions below are guards, not
    logic.
    """
    if body.raw_messages is not None:
        return body.raw_messages, None
    if body.prompt_version_id is not None:
        version_row = await pool.fetchrow(
            """select id, prompt_id, template, template_messages
                 from prompt_version where id = $1""",
            body.prompt_version_id,
        )
        if version_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "prompt version not found")
        msgs_raw = version_row["template_messages"]
        if isinstance(msgs_raw, str):
            msgs_raw = _json.loads(msgs_raw)
        messages = [Message.model_validate(m) for m in msgs_raw]
        return messages, version_row
    # raw_template path
    assert body.raw_template is not None  # validated above
    return [Message(role="human", content=body.raw_template)], None


def _coerce_var_value(value: Any) -> str:
    """Stringify a variable value the same way for both render paths.

    Strings pass through. Other types serialize via json.dumps so dicts
    and lists round-trip as readable JSON. Falls back to str() on
    json-incompatible objects (datetimes etc.) so we never crash mid-render.
    """
    if isinstance(value, str):
        return value
    try:
        return _json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _render_messages(
    messages: list[Message],
    variables: dict[str, Any],
) -> list[Message]:
    """Render `{{ var }}` substitutions in each message's content.

    Missing variables render as empty string per spec decision 9 — the
    user can iterate without the renderer fighting them. Non-string
    values serialize via json.dumps (via _coerce_var_value) so a dict or
    list passed as a variable round-trips as readable JSON.

    Returns a fresh list of new Message objects — never mutates the
    input list or its contents.
    """

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            return ""  # spec decision 9: missing var -> empty string
        return _coerce_var_value(variables[key])

    return [Message(role=m.role, content=_VAR_RE.sub(_repl, m.content)) for m in messages]


# Single source of truth for the prompt -> dispatch role translation.
# When AI / tool roles land (spec decision 2 deferral), extend this dict
# and the prompt-side Message Literal in routers/prompts.py together.
_PROMPT_TO_DISPATCH_ROLE: Mapping[
    Literal["system", "human"],
    Literal["system", "user", "assistant", "tool"],
] = {
    "system": "system",
    "human": "user",
}


def _to_dispatch_messages(messages: list[Message]) -> list[DispatchMessage]:
    """Convert prompt-side roles to dispatcher roles.

    Prompt side: `system` | `human` (LangSmith vocabulary).
    Dispatch side: `system` | `user` | `assistant` | `tool` (provider
    vocabulary, what LiteLLM expects).

    The only translation today is `human` -> `user`; system passes
    through. AI / tool roles are deferred (spec decision 2). When they
    land, extend `_PROMPT_TO_DISPATCH_ROLE` AND the prompt-side
    `Message.role` Literal in `routers/prompts.py` in the same change —
    the dispatch-side dataclass already accepts assistant / tool, so
    only the prompt side and the bridging table need updating.
    """
    return [
        DispatchMessage(
            role=_PROMPT_TO_DISPATCH_ROLE[m.role],
            content=m.content,
        )
        for m in messages
    ]


async def _dispatch_stub(model: str, prompt: str) -> dict[str, Any]:
    """Deterministic echo for tests + the no-key smoke path. The
    'stub' provider bypasses the LiteLLM gateway entirely."""
    return {
        "text": f"[stub:{model}] {prompt[-512:]}",
        "prompt_tokens": _approx_tokens(prompt),
        "completion_tokens": _approx_tokens(prompt[-512:]),
    }


def _approx_tokens(text: str) -> int:
    # Stub-only token estimate; real provider responses come back from
    # LiteLLM with actual usage numbers. 4 chars/token is the rule of
    # thumb that's wrong by ±30%.
    return max(1, len(text) // 4)


async def _write_clickhouse_trace(
    clickhouse: ClickHouseQuery | None,
    *,
    org_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    run_id: str,
    model: str,
    temperature: float | None,
    prompt: str,
    output: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    session_id: str,
    user_id: str,
) -> None:
    """Best-effort trace write so the playground call shows up at /runs.

    If ClickHouse is unreachable we log and continue -- the postgres
    session row still records the call and its output. ER-23: never
    silent-drop the attempt; surfacing "trace plane unavailable" beats
    pretending nothing happened.
    """
    if clickhouse is None:
        log.info("clickhouse not configured; skipping playground trace write")
        return
    now = datetime.now(UTC)
    inputs_json = _json.dumps({"prompt": prompt})
    outputs_json = _json.dumps({"output": output})
    total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    try:
        await clickhouse.insert(
            "run",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    run_id,
                    None,
                    "playground",
                    "llm",
                    "ok",
                    "playground",
                    now,
                    now,
                    now,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(total_tokens),
                    0,
                    session_id,
                    user_id,
                    ["playground"],
                    _json.dumps({"playground": True}),
                    "",
                    "",
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "parent_run_id",
                "name",
                "kind",
                "status",
                "sdk",
                "start_time",
                "end_time",
                "received_at",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "session_id",
                "user_id",
                "tags",
                "metadata",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
        await clickhouse.insert(
            "span",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    run_id,
                    str(uuid.uuid4()),
                    None,
                    f"playground:{model}",
                    "llm",
                    "ok",
                    now,
                    now,
                    now,
                    model,
                    temperature,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(total_tokens),
                    0,
                    _json.dumps({"playground": True}),
                    "",
                    "",
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "span_id",
                "parent_span_id",
                "name",
                "kind",
                "status",
                "start_time",
                "end_time",
                "received_at",
                "model",
                "temperature",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "attributes",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "playground clickhouse trace write failed",
            run_id=run_id,
            error=str(exc),
        )


async def _assert_project_role(
    pool: asyncpg.Pool,
    principal: Principal,
    project_id: UUID,
    allowed: tuple[str, ...],
) -> UUID:
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
        allowed=allowed,
    )
    return workspace_id


def _session_out(row: asyncpg.Record) -> PlaygroundSessionOut:
    return PlaygroundSessionOut(
        id=row["id"],
        project_id=row["project_id"],
        prompt_version_id=row["prompt_version_id"],
        raw_template=row["raw_template"],
        rendered_prompt=row["rendered_prompt"],
        variables=_coerce_json(row["variables"]),
        provider=row["provider"],
        model=row["model"],
        temperature=row["temperature"],
        max_tokens=row["max_tokens"],
        status=row["status"],
        output_text=row["output_text"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        cost_usd=row["cost_usd"],
        latency_ms=row["latency_ms"],
        run_id=row["run_id"],
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _coerce_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}
