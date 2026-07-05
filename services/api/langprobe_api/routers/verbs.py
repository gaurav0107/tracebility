"""HTTP router for the 5 agent-native eval-loop verbs (Task 7, D2-A).

Thin wiring layer over the framework-agnostic verb service layer in
``langprobe_api/verbs/``. This router does three jobs and nothing else:

1. Auth — every route requires a session principal
   (``Depends(require_user)``). There is no API-key path on the api yet;
   when Task 8's MCP adapter adds one, ``POST /v1/verbs/promote`` MUST
   stay session-gated (see the comment on that route below) — promoting
   a draft mutates production judge config and is the human-approval
   choke point in the design.
2. Scope — resolves the request's ``project_id`` via
   ``tenant_scope.resolve_project_scope`` (403/404 if the caller can't
   access that project), then builds a ``TenantContext`` for the verb
   call. For session calls there is no api-key row, so
   ``TenantContext.api_key_id`` carries the acting user's id instead —
   this is how ``created_by``-style columns end up populated with a
   user id rather than an api key id on this path.
3. Exception mapping — verb-layer domain exceptions become HTTP
   responses via ``_call_verb``.

``POST /v1/verbs/backtest`` is the one route that schedules background
work: ``run_judge_over_cohort`` (the sync setup half) runs inline and
returns 202 with a queued ``BacktestOut``; the executor
(``_run_backtest``) is scheduled via ``BackgroundTasks.add_task`` here —
this is the ONLY place ``BackgroundTasks`` is wired. The verb layer
itself never imports FastAPI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

import asyncpg
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from langprobe_tenant.context import TenantContext

from ..auth import Principal, require_user
from ..clickhouse_client import ClickHouseQuery
from ..llm.types import DispatchError
from ..tenant_scope import resolve_project_scope
from ..verbs.backtest import _run_backtest
from ..verbs.deps import VerbDeps
from ..verbs.models import (
    BacktestOut,
    BacktestRequest,
    ClusterFailuresIn,
    ClusterFailuresOut,
    EvalDraftOut,
    PromoteOut,
    PromoteRequest,
    ProposeEvalIn,
    WatchOut,
    WatchRequest,
)
from ..verbs.promote import ApprovalRequiredError, DraftNotReadyError
from ..verbs.propose import ProposerFailedError
from ..verbs.scope import ScopeError
from ..verbs.service import (
    cluster_failures,
    promote_to_recurring,
    propose_eval,
    run_judge_over_cohort,
    watch_judge,
)

log = structlog.get_logger("langprobe.api.verbs")

router = APIRouter(prefix="/v1/verbs", tags=["verbs"])

_T = TypeVar("_T")


async def _call_verb(fn: Callable[[], Awaitable[_T]]) -> _T:
    """Run a verb call, mapping its domain exceptions to HTTP responses.

    Framework-agnostic exceptions raised by the verb layer are the only
    ones handled here — anything else propagates as an unhandled 500,
    same as any other route.
    """
    try:
        return await fn()
    except ScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except DraftNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProposerFailedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except DispatchError as exc:
        log.warning("verb dispatch error", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "dispatch unavailable") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        log.warning("verb data-plane error", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc


def _require_clickhouse(request: Request) -> ClickHouseQuery:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ch


async def _build_ctx_and_deps(
    request: Request,
    project_id,
    principal: Principal,
    *,
    allowed_roles: Sequence[str] = ("owner", "admin", "member", "viewer"),
) -> tuple[TenantContext, VerbDeps]:
    """Resolve project scope, then build the (TenantContext, VerbDeps) pair
    every verb call needs. Raises 403/404 (via ``resolve_project_scope``) if
    the principal can't access ``project_id``, or doesn't hold one of
    ``allowed_roles`` in the project's workspace. Callers that spend LLM
    money or mutate production judge config must narrow ``allowed_roles``
    to exclude viewer (and, for promote, member) — see call sites below."""
    pool: asyncpg.Pool = request.app.state.pg
    scope = await resolve_project_scope(pool, project_id, principal, allowed_roles=allowed_roles)
    ctx = TenantContext(
        org_id=scope.org_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        # No api-key row on the session path — the acting user's id
        # stands in, so verb-layer `created_by` columns end up carrying
        # the user id rather than an api key id.
        api_key_id=principal.user_id,
        plan="self_hosted",
        scopes=frozenset({"verbs:*"}),
    )
    deps = VerbDeps(pool=pool, ch=_require_clickhouse(request))
    return ctx, deps


@router.post("/cluster-failures", response_model=ClusterFailuresOut)
async def post_cluster_failures(
    request: Request,
    body: ClusterFailuresIn,
    principal: Principal = Depends(require_user),
) -> ClusterFailuresOut:
    # Read-only triage — viewer is fine (default allowed_roles).
    ctx, deps = await _build_ctx_and_deps(request, body.project_id, principal)
    return await _call_verb(lambda: cluster_failures(deps, ctx, body))


@router.post("/propose-eval", response_model=EvalDraftOut)
async def post_propose_eval(
    request: Request,
    body: ProposeEvalIn,
    principal: Principal = Depends(require_user),
) -> EvalDraftOut:
    # Dispatches an LLM call to draft the judge — viewer must not trigger spend.
    ctx, deps = await _build_ctx_and_deps(
        request, body.project_id, principal, allowed_roles=("owner", "admin", "member")
    )
    return await _call_verb(lambda: propose_eval(deps, ctx, body))


@router.post("/backtest", response_model=BacktestOut, status_code=status.HTTP_202_ACCEPTED)
async def post_backtest(
    request: Request,
    body: BacktestRequest,
    background: BackgroundTasks,
    principal: Principal = Depends(require_user),
) -> BacktestOut:
    # Dispatches N judge calls over the cohort — viewer must not trigger spend.
    ctx, deps = await _build_ctx_and_deps(
        request, body.project_id, principal, allowed_roles=("owner", "admin", "member")
    )
    out = await _call_verb(lambda: run_judge_over_cohort(deps, ctx, body.to_verb_params()))
    background.add_task(_run_backtest, deps, out.backtest_run_id)
    return out


@router.post("/promote", response_model=PromoteOut)
async def post_promote(
    request: Request,
    body: PromoteRequest,
    principal: Principal = Depends(require_user),
) -> PromoteOut:
    # SECURITY BOUNDARY: `require_user` (session cookie) is the ONLY auth
    # path wired here on purpose. Promoting a draft mutates production
    # judge config, so this route must never be reachable by an
    # api-key/agent caller. When Task 8's MCP adapter (or a future
    # api-key auth path) lands, `promote` must stay gated behind a real
    # human session — do not swap this dependency for an api-key
    # principal, and do not add an alternate api-key-authenticated route
    # to this same verb.
    #
    # Role gate: promoting creates a recurring production judge — the
    # human-approval choke point in the design — so member (and viewer)
    # must not be able to trigger it; only owner/admin may.
    ctx, deps = await _build_ctx_and_deps(
        request, body.project_id, principal, allowed_roles=("owner", "admin")
    )
    return await _call_verb(lambda: promote_to_recurring(deps, ctx, body.to_verb_params()))


@router.post("/watch", response_model=WatchOut)
async def post_watch(
    request: Request,
    body: WatchRequest,
    principal: Principal = Depends(require_user),
) -> WatchOut:
    # Read-only status poll — viewer is fine (default allowed_roles).
    ctx, deps = await _build_ctx_and_deps(request, body.project_id, principal)
    return await _call_verb(lambda: watch_judge(deps, ctx, body.to_verb_params()))
