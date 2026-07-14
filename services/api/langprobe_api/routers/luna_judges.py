"""Luna prompted-judges — LLM-as-judge with a user-authored rubric.

An operator writes a rubric prompt that scores any
``(input, expected, output)`` tuple on a 0..1 scale plus a short
rationale. The runner dispatches the prompt to the configured LLM
provider, parses the response, and writes one ``eval_score`` row
tagged ``judge_name='luna:<slug>'`` so the analytics shape stays
uniform with built-in / human / cmp:* / poll-panel judges.

V1 honest scope:
- CRUD over a project-scoped catalog of judges (slug + rubric +
  provider/model + temperature/max_tokens).
- A judge is referenced by slug at eval/poll-run time as
  ``luna:<slug>``; the existing routers learn that prefix and dispatch
  through ``apply_luna_judge`` below. Built-in judges
  (echo/contains/exact) keep working unchanged.
- Soft-delete on the catalog row keeps historical eval_score rows
  intelligible.

Boundary:
- Provider creds come from env (ANTHROPIC_API_KEY / OPENAI_API_KEY),
  same as the playground. Per-workspace encrypted creds slot in a
  later iteration without changing this URL surface.
- Audit-fail-closed (ER-10) on every catalog write.
- RBAC: list/get for any role; create/patch for owner/admin/member;
  delete (soft) owner/admin only.
- Runner failures (provider 5xx, parse errors) are recorded as
  ``outcome='failed'`` on the eval_score row with the error text in
  ``raw_output``; we never silent-drop (ER-23). Bad responses get
  score=0 + label='parse-error' so aggregates degrade gracefully.
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user

log = structlog.get_logger("langprobe.api.luna_judges")

router = APIRouter(prefix="/v1/luna-judges", tags=["luna-judges"])

_VALID_PROVIDERS = {
    "anthropic",
    "openai",
    "gemini",
    "mistral",
    "deepseek",
    "groq",
    "stub",
}
_VALID_FORMATS = {"score-rationale", "json-object"}
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_SCORE_RE = re.compile(r"score\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_RATIONALE_RE = re.compile(r"rationale\s*[:=]\s*(.+)", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


class LunaJudgeOut(BaseModel):
    id: UUID
    project_id: UUID
    slug: str
    name: str
    description: str | None
    rubric_prompt: str
    output_format: str
    provider: str
    model: str
    temperature: float | None
    max_tokens: int
    created_at: datetime
    updated_at: datetime


class LunaJudgeCreate(BaseModel):
    project_id: UUID
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rubric_prompt: str = Field(min_length=1, max_length=20000)
    output_format: str = Field(default="score-rationale", max_length=24)
    provider: str = Field(min_length=1, max_length=16)
    model: str = Field(min_length=1, max_length=128)
    temperature: float | None = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)


class LunaJudgePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rubric_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    output_format: str | None = Field(default=None, max_length=24)
    provider: str | None = Field(default=None, min_length=1, max_length=16)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[LunaJudgeOut])
async def list_judges(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[LunaJudgeOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, slug, name, description, rubric_prompt,
               output_format, provider, model, temperature, max_tokens,
               created_at, updated_at
          from luna_judge
         where project_id = $1 and deleted_at is null
         order by created_at desc
        """,
        project_id,
    )
    return [LunaJudgeOut(**dict(r)) for r in rows]


@router.post(
    "",
    response_model=LunaJudgeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_judge(
    request: Request,
    body: LunaJudgeCreate,
    principal: Principal = Depends(require_user),
) -> LunaJudgeOut:
    if body.provider not in _VALID_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"provider must be one of {sorted(_VALID_PROVIDERS)}",
        )
    if body.output_format not in _VALID_FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"output_format must be one of {sorted(_VALID_FORMATS)}",
        )
    if not _SLUG_RE.match(body.slug):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "slug must match ^[a-z0-9][a-z0-9_-]*$",
        )

    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )

    try:
        row = await pool.fetchrow(
            """
            insert into luna_judge (
                project_id, slug, name, description, rubric_prompt,
                output_format, provider, model, temperature, max_tokens,
                created_by
            )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            returning id, project_id, slug, name, description, rubric_prompt,
                      output_format, provider, model, temperature, max_tokens,
                      created_at, updated_at
            """,
            body.project_id,
            body.slug,
            body.name,
            body.description,
            body.rubric_prompt,
            body.output_format,
            body.provider,
            body.model,
            body.temperature,
            body.max_tokens,
            principal.user_id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a judge with slug '{body.slug}' already exists in this project",
        ) from exc
    assert row is not None

    await audit.record(
        pool,
        principal=principal,
        action="luna_judge.create",
        target_kind="luna_judge",
        target_id=row["id"],
        payload={
            "slug": body.slug,
            "provider": body.provider,
            "model": body.model,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=body.project_id,
    )
    return LunaJudgeOut(**dict(row))


@router.get("/{judge_id}", response_model=LunaJudgeOut)
async def get_judge(
    request: Request,
    judge_id: UUID,
    principal: Principal = Depends(require_user),
) -> LunaJudgeOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_judge(pool, judge_id)
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    return LunaJudgeOut(**dict(row))


@router.patch("/{judge_id}", response_model=LunaJudgeOut)
async def patch_judge(
    request: Request,
    judge_id: UUID,
    body: LunaJudgePatch,
    principal: Principal = Depends(require_user),
) -> LunaJudgeOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_judge(pool, judge_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(
        pool, principal, project_id, ("owner", "admin", "member")
    )

    if body.provider is not None and body.provider not in _VALID_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"provider must be one of {sorted(_VALID_PROVIDERS)}",
        )
    if body.output_format is not None and body.output_format not in _VALID_FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"output_format must be one of {sorted(_VALID_FORMATS)}",
        )

    sets: list[str] = []
    args: list[Any] = []
    for field_name, value in body.model_dump(exclude_none=True).items():
        args.append(value)
        sets.append(f"{field_name} = ${len(args)}")

    if not sets:
        return LunaJudgeOut(**dict(row))

    args.append(judge_id)
    updated = await pool.fetchrow(
        f"""
        update luna_judge
           set {", ".join(sets)}
         where id = ${len(args)}
        returning id, project_id, slug, name, description, rubric_prompt,
                  output_format, provider, model, temperature, max_tokens,
                  created_at, updated_at
        """,
        *args,
    )
    assert updated is not None

    await audit.record(
        pool,
        principal=principal,
        action="luna_judge.update",
        target_kind="luna_judge",
        target_id=judge_id,
        payload={"fields": [s.split(" ")[0] for s in sets]},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return LunaJudgeOut(**dict(updated))


@router.delete("/{judge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_judge(
    request: Request,
    judge_id: UUID,
    principal: Principal = Depends(require_user),
) -> None:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_judge(pool, judge_id)
    project_id: UUID = row["project_id"]
    workspace_id = await _assert_project_role(pool, principal, project_id, ("owner", "admin"))
    await pool.execute(
        "update luna_judge set deleted_at = now() where id = $1",
        judge_id,
    )
    await audit.record(
        pool,
        principal=principal,
        action="luna_judge.delete",
        target_kind="luna_judge",
        target_id=judge_id,
        payload={"slug": row["slug"]},
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Public dispatch helpers — used by evals.py and poll_runs.py
# ---------------------------------------------------------------------------


async def resolve_judge(pool: asyncpg.Pool, project_id: UUID, slug: str) -> dict[str, Any] | None:
    """Look up a luna judge by slug; returns None if missing/deleted."""
    row = await pool.fetchrow(
        """
        select id, slug, rubric_prompt, output_format, provider, model,
               temperature, max_tokens
          from luna_judge
         where project_id = $1 and slug = $2 and deleted_at is null
        """,
        project_id,
        slug,
    )
    return dict(row) if row else None


def parse_judge_kind(judge_kind: str) -> tuple[str, str | None]:
    """Split 'luna:my-judge' → ('luna', 'my-judge'); else (kind, None)."""
    if judge_kind.startswith("luna:"):
        return "luna", judge_kind[len("luna:") :]
    return judge_kind, None


async def apply_luna_judge(
    judge_row: dict[str, Any],
    *,
    pool: asyncpg.Pool,
    project_id: UUID,
    surface: str,
    surface_ref_id: UUID,
    input_text: str,
    expected: str,
    output_text: str | None = None,
) -> tuple[float, str, str, str, float]:
    """Run the prompted judge once via the LiteLLM gateway.

    Returns ``(score, label, rationale, raw_output, cost_usd)``.

    The runner never raises on a provider failure — it returns
    ``(0.0, 'error', '<reason>', '<details>')`` so the eval_score
    write stays consistent. ER-23: never silent-drop.
    """
    rendered = _render_rubric(
        judge_row["rubric_prompt"],
        {
            "input": input_text,
            "expected": expected,
            "output": output_text if output_text is not None else input_text,
        },
    )
    provider = judge_row["provider"]
    model = judge_row["model"]
    if provider == "stub":
        # Deterministic stub for tests + smoke. The parser path is
        # exercised; the LiteLLM gateway is bypassed.
        raw = f"score: 1.0\nrationale: stub-judge ack ({rendered[-128:]})"
        score, label, rationale = _parse_response(
            raw,
            judge_row.get("output_format") or "score-rationale",
        )
        return score, label, rationale, raw[:2000], 0.0

    bare_model = model
    if not bare_model.startswith(provider + "/"):
        bare_model = f"{provider}/{bare_model}"
    from ..llm import DispatchError, Message
    from ..llm import dispatch as gateway_dispatch

    try:
        result = await gateway_dispatch(
            pool,
            project_id=project_id,
            surface=surface,  # type: ignore[arg-type]
            surface_ref_id=surface_ref_id,
            model=bare_model,
            messages=[Message(role="user", content=rendered)],
            temperature=judge_row.get("temperature"),
            max_tokens=int(judge_row.get("max_tokens") or 512),
        )
    except DispatchError as exc:
        log.warning(
            "luna judge dispatch failed",
            slug=judge_row.get("slug"),
            code=exc.code,
            detail=exc.detail,
        )
        return 0.0, "error", f"dispatch failed: [{exc.code}] {exc.detail}", exc.detail[:1000], 0.0

    raw = result.text
    score, label, rationale = _parse_response(
        raw,
        judge_row.get("output_format") or "score-rationale",
    )
    return score, label, rationale, raw[:2000], float(result.cost_usd or 0.0)


# ---------------------------------------------------------------------------
# Internal: rendering, dispatch, parsing
# ---------------------------------------------------------------------------


def _render_rubric(template: str, variables: dict[str, Any]) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            return match.group(0)
        value = variables[key]
        if isinstance(value, str):
            return value
        try:
            return _json.dumps(value)
        except (TypeError, ValueError):
            return str(value)

    return _VAR_RE.sub(_repl, template)


def _parse_response(text: str, output_format: str) -> tuple[float, str, str]:
    """Parse the model's response into (score, label, rationale).

    Two parse strategies:
      - 'score-rationale' (default): looks for ``score: <number>``
        and ``rationale: <text>`` lines (case-insensitive).
      - 'json-object': expects a JSON object somewhere in the
        response with at least a ``score`` field; ``rationale`` and
        ``label`` are optional.
    """
    if output_format == "json-object":
        return _parse_json_response(text)

    score_match = _SCORE_RE.search(text)
    if score_match is None:
        return 0.0, "parse-error", text[:500]
    try:
        score = float(score_match.group(1))
    except ValueError:
        return 0.0, "parse-error", text[:500]
    score = max(0.0, min(1.0, score))

    rationale = ""
    rationale_match = _RATIONALE_RE.search(text[score_match.end() :])
    if rationale_match:
        rationale = rationale_match.group(1).strip()
    if not rationale:
        # Pull the first non-empty line that isn't the score line.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not _SCORE_RE.match(stripped):
                rationale = stripped
                break

    label = "pass" if score >= 0.5 else "fail"
    return score, label, rationale


def _parse_json_response(text: str) -> tuple[float, str, str]:
    # Find the first {...} block; tolerant of leading prose.
    depth = 0
    start = -1
    end = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                end = i + 1
                break
    if start == -1 or end == -1:
        return 0.0, "parse-error", text[:500]
    try:
        payload = _json.loads(text[start:end])
    except _json.JSONDecodeError:
        return 0.0, "parse-error", text[:500]
    score_v = payload.get("score") if isinstance(payload, dict) else None
    try:
        score = float(score_v)
    except (TypeError, ValueError):
        return 0.0, "parse-error", text[:500]
    score = max(0.0, min(1.0, score))
    rationale = str(payload.get("rationale") or "")
    label = str(payload.get("label") or ("pass" if score >= 0.5 else "fail"))
    return score, label, rationale


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_judge(pool: asyncpg.Pool, judge_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, slug, name, description, rubric_prompt,
               output_format, provider, model, temperature, max_tokens,
               created_at, updated_at
          from luna_judge
         where id = $1 and deleted_at is null
        """,
        judge_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "luna judge not found")
    return row


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
