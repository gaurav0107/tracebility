"""LiteLLM-backed dispatch gateway — single entry for every LLM call.

Caller surfaces (playground / comparisons / studio / luna / poll_runs /
eval runner) call `dispatch(...)`. We resolve credentials, check the
project ceiling on automated surfaces, hand off to litellm, normalize
the response, and write one dispatch_cost row.

No caller imports litellm. If we ever swap libraries, this is the only
file that changes.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID

import asyncpg
import litellm
import structlog
from litellm import exceptions as litellm_errors

from .. import audit
from ..routers.llm_credentials import resolve_secret
from .audit_throttle import should_emit_audit
from .types import (
    DispatchError,
    DispatchResult,
    Message,
    SurfaceName,
    provider_from_model,
)

log = structlog.get_logger("langprobe.llm.gateway")

# Hygiene: pin LiteLLM into the shape we want at import time.
litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.set_verbose = False
litellm.telemetry = False

_AUTOMATED_SURFACES: frozenset[str] = frozenset(["luna", "eval", "poll"])


async def _workspace_id_for_project(pool: asyncpg.Pool, project_id: UUID) -> UUID:
    ws = await pool.fetchval("select workspace_id from project where id = $1", project_id)
    if ws is None:
        raise DispatchError("no_credential", None, f"unknown project {project_id}")
    return ws


async def _check_ceiling(pool: asyncpg.Pool, project_id: UUID) -> None:
    """Raise ceiling_exceeded if rolling-24h spend ≥ project ceiling.
    Called only on automated surfaces. Caller catches and translates."""
    row = await pool.fetchrow(
        """
        select p.eval_cost_ceiling_usd_per_day as ceiling,
               coalesce((
                   select sum(cost_usd) from dispatch_cost
                    where project_id = p.id
                      and dispatched_at > now() - interval '24 hours'
               ), 0) as spent
          from project p
         where p.id = $1
        """,
        project_id,
    )
    if row is None or row["ceiling"] is None:
        return
    ceiling = float(row["ceiling"])
    spent = float(row["spent"] or 0)
    if spent >= ceiling:
        raise DispatchError(
            "ceiling_exceeded",
            None,
            f"24h spend ${spent:.2f} ≥ ceiling ${ceiling:.2f}",
        )


async def _record_cost(
    pool: asyncpg.Pool,
    *,
    project_id: UUID,
    workspace_id: UUID,
    surface: str,
    surface_ref_id: UUID,
    provider: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cost_usd: float,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    detail = error_detail[:500] if error_detail else None
    await pool.execute(
        """
        insert into dispatch_cost (
            project_id, workspace_id, surface, surface_ref_id,
            provider, model, prompt_tokens, completion_tokens,
            cost_usd, error_code, error_detail
        )
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        project_id,
        workspace_id,
        surface,
        surface_ref_id,
        provider,
        model,
        prompt_tokens,
        completion_tokens,
        cost_usd,
        error_code,
        detail,
    )


async def dispatch(
    pool: asyncpg.Pool,
    *,
    project_id: UUID,
    surface: SurfaceName,
    surface_ref_id: UUID,
    model: str,
    messages: list[Message],
    temperature: float | None = None,
    max_tokens: int = 2048,
    timeout_s: float = 60.0,
    extra: dict[str, Any] | None = None,
) -> DispatchResult:
    """Single entry-point for every LLM call.

    On success: writes one dispatch_cost row, returns DispatchResult.
    On failure: writes one dispatch_cost row with error_code set,
    raises DispatchError. Callers translate the error to their own
    row-failure shape.
    """
    from ..config import load as _load_settings

    if _load_settings().llm_gateway != "litellm":
        raise DispatchError(
            "provider_error",
            None,
            "LLM_GATEWAY != litellm: dispatch refused. "
            "Set LLM_GATEWAY=litellm or roll back the deploy.",
        )

    provider = provider_from_model(model)
    workspace_id = await _workspace_id_for_project(pool, project_id)

    if provider == "stub":
        # Deterministic echo, no credential, zero cost. Keeps every
        # LLM-dispatching surface (studio replay, comparisons, judges)
        # exercisable on a keyless self-host — same contract the
        # playground's stub-* models already honor.
        prompt_text = "\n\n".join(m.content for m in messages)
        text = f"[{model}] {prompt_text[-512:]}"
        prompt_tokens = max(1, len(prompt_text) // 4)
        completion_tokens = max(1, len(text) // 4)
        await _record_cost(
            pool,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface,
            surface_ref_id=surface_ref_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0,
            error_code=None,
            error_detail=None,
        )
        return DispatchResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            provider=provider,
            model=model,
        )

    if surface in _AUTOMATED_SURFACES:
        try:
            await _check_ceiling(pool, project_id)
        except DispatchError as exc:
            await _record_cost(
                pool,
                project_id=project_id,
                workspace_id=workspace_id,
                surface=surface,
                surface_ref_id=surface_ref_id,
                provider=provider,
                model=model,
                prompt_tokens=None,
                completion_tokens=None,
                cost_usd=0,
                error_code=exc.code,
                error_detail=exc.detail,
            )
            if await should_emit_audit(
                pool,
                project_id=project_id,
                provider=provider,
                action="dispatch.ceiling_exceeded",
            ):
                await audit.record(
                    pool,
                    principal=None,
                    action="dispatch.ceiling_exceeded",
                    target_kind="project",
                    target_id=project_id,
                    payload={
                        "provider": provider,
                        "surface": surface,
                        "detail": exc.detail,
                    },
                    project_id=project_id,
                    workspace_id=workspace_id,
                )
            raise

    api_key = await resolve_secret(pool, project_id=project_id, provider=provider)
    if api_key is None:
        await _record_cost(
            pool,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface,
            surface_ref_id=surface_ref_id,
            provider=provider,
            model=model,
            prompt_tokens=None,
            completion_tokens=None,
            cost_usd=0,
            error_code="no_credential",
            error_detail=f"no {provider} credential resolved",
        )
        if await should_emit_audit(
            pool,
            project_id=project_id,
            provider=provider,
            action="dispatch.no_credential",
        ):
            await audit.record(
                pool,
                principal=None,
                action="dispatch.no_credential",
                target_kind="project",
                target_id=project_id,
                payload={"provider": provider, "surface": surface},
                project_id=project_id,
                workspace_id=workspace_id,
            )
        raise DispatchError(
            "no_credential",
            provider,
            f"no {provider} credential resolved",
        )

    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[dataclasses.asdict(m) for m in messages],
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_s,
            num_retries=0,
            **(extra or {}),
        )
    except litellm_errors.AuthenticationError as exc:
        detail = str(exc)[:500]
        await _record_cost(
            pool,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface,
            surface_ref_id=surface_ref_id,
            provider=provider,
            model=model,
            prompt_tokens=None,
            completion_tokens=None,
            cost_usd=0,
            error_code="no_credential",
            error_detail=detail,
        )
        raise DispatchError("no_credential", provider, detail) from exc
    except litellm_errors.Timeout as exc:
        detail = str(exc)[:500]
        await _record_cost(
            pool,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface,
            surface_ref_id=surface_ref_id,
            provider=provider,
            model=model,
            prompt_tokens=None,
            completion_tokens=None,
            cost_usd=0,
            error_code="timeout",
            error_detail=detail,
        )
        raise DispatchError("timeout", provider, detail) from exc
    except (litellm_errors.APIError, litellm_errors.RateLimitError) as exc:
        detail = str(exc)[:500]
        await _record_cost(
            pool,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface,
            surface_ref_id=surface_ref_id,
            provider=provider,
            model=model,
            prompt_tokens=None,
            completion_tokens=None,
            cost_usd=0,
            error_code="provider_error",
            error_detail=detail,
        )
        raise DispatchError("provider_error", provider, detail) from exc

    text = resp.choices[0].message.content or ""
    usage = resp.usage or {}
    if hasattr(usage, "get"):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
    try:
        cost_usd = float(litellm.completion_cost(completion_response=resp) or 0)
    except Exception:  # pragma: no cover — LiteLLM cost is best-effort
        cost_usd = 0.0

    await _record_cost(
        pool,
        project_id=project_id,
        workspace_id=workspace_id,
        surface=surface,
        surface_ref_id=surface_ref_id,
        provider=provider,
        model=getattr(resp, "model", model) or model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        error_code=None,
        error_detail=None,
    )

    return DispatchResult(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd if cost_usd > 0 else None,
        provider=provider,
        model=getattr(resp, "model", model) or model,
        raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
    )
