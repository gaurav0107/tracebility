"""promote_to_recurring verb (Task 6, D2-A).

A ``backtest_draft`` that reached ``ready`` (a human has reviewed its
backtest results) can be promoted into a real, recurring ``luna_judge``.
Two security/robustness properties under test:

- APPROVAL GATE: an empty/blank ``approval_token`` must reject the call
  before anything is written. This is a defense-in-depth check — the
  real "humans only" enforcement is the router's auth layer (Task 7) —
  but the verb itself must not trust a blank token.
- IDEMPOTENCY: the judge's slug is derived deterministically from a
  hash of ``draft.judge_config``, so a retried promote call (e.g. an
  agent that didn't see the first call's response) never creates a
  second judge — it converges on the same row via the unique
  constraint on (project_id, slug).

Everything here mocks ``deps.pool`` (asyncpg) — no real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import PromoteIn
from langprobe_api.verbs.promote import (
    ApprovalRequiredError,
    DraftNotReadyError,
    _config_hash,
    _slug_for_config,
    promote_to_recurring,
)
from langprobe_api.verbs.scope import ScopeError
from langprobe_tenant.context import TenantContext


def _make_ctx(project_id) -> TenantContext:
    return TenantContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        project_id=project_id,
        api_key_id=uuid4(),
        plan="pro",
        scopes=frozenset({"verbs:*"}),
    )


def _draft_row(project_id, *, status="ready", judge_config=None):
    return {
        "id": uuid4(),
        "project_id": project_id,
        "org_id": uuid4(),
        "cluster_ref": {},
        "judge_kind": "luna:proposed",
        "judge_config": judge_config
        if judge_config is not None
        else {"prompt": "flag hallucinations", "threshold": 0.5, "label": "fail"},
        "status": status,
        "created_by": uuid4(),
        "created_at": datetime.now(UTC),
        "heartbeat_at": None,
        "error": None,
    }


def _make_pool(mocker, *, fetchrow_rows=None, fetchrow_side_effect=None):
    pool = mocker.MagicMock(name="pool")
    if fetchrow_side_effect is not None:
        pool.fetchrow = mocker.AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = mocker.AsyncMock(side_effect=fetchrow_rows or [])
    pool.execute = mocker.AsyncMock(return_value="UPDATE 1")
    return pool


def _make_deps(pool) -> VerbDeps:
    return VerbDeps(pool=pool, ch=None)


# ----- happy path ------------------------------------------------------


async def test_promote_happy_path_creates_judge_and_marks_draft_promoted(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id)
    judge_id = uuid4()

    pool = _make_pool(
        mocker,
        fetchrow_rows=[
            draft,  # draft lookup
            {"id": judge_id},  # luna_judge insert ... returning id
        ],
    )
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token="approved-by-alice")

    out = await promote_to_recurring(deps, ctx, params)

    assert out.judge_id == judge_id

    insert_call = next(
        c for c in pool.fetchrow.await_args_list if "insert into luna_judge" in c.args[0]
    )
    assert draft["judge_config"]["prompt"] in insert_call.args
    # created_by is the acting user's api_key_id, not hardcoded None.
    assert ctx.api_key_id in insert_call.args

    update_call = next(
        c for c in pool.execute.await_args_list if "update backtest_draft" in c.args[0]
    )
    assert "promoted" in str(update_call.args)


# ----- idempotency -------------------------------------------------------


async def test_promote_idempotent_on_unique_violation_returns_existing_judge(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id)
    existing_judge_id = uuid4()

    async def fetchrow_side_effect(query, *args, **kwargs):
        if "select id, project_id" in query and "backtest_draft" in query:
            return draft
        if "insert into luna_judge" in query:
            raise asyncpg.UniqueViolationError("duplicate key")
        if "select id from luna_judge" in query:
            return {"id": existing_judge_id}
        raise AssertionError(f"unexpected query: {query}")

    pool = _make_pool(mocker, fetchrow_side_effect=fetchrow_side_effect)
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token="approved-by-alice")

    out = await promote_to_recurring(deps, ctx, params)

    assert out.judge_id == existing_judge_id
    # No duplicate judge should have been created — the fallback SELECT
    # is the only "success" path after the UniqueViolationError.


async def test_promote_same_judge_config_yields_same_slug():
    config_a = {"prompt": "flag hallucinations", "threshold": 0.5, "label": "fail"}
    config_b = {"label": "fail", "threshold": 0.5, "prompt": "flag hallucinations"}  # reordered

    assert _config_hash(config_a) == _config_hash(config_b)
    assert _slug_for_config(config_a) == _slug_for_config(config_b)


# ----- approval gate ------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   "])
async def test_promote_blank_approval_token_raises_and_inserts_nothing(mocker, token):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id)
    pool = _make_pool(mocker, fetchrow_rows=[draft])
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token=token)

    with pytest.raises(ApprovalRequiredError):
        await promote_to_recurring(deps, ctx, params)

    insert_calls = [
        c for c in pool.fetchrow.await_args_list if "insert into luna_judge" in c.args[0]
    ]
    assert insert_calls == []


# ----- draft status gate ---------------------------------------------------


async def test_promote_draft_not_ready_raises_and_inserts_nothing(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id, status="drafting")
    pool = _make_pool(mocker, fetchrow_rows=[draft])
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token="approved-by-alice")

    with pytest.raises(DraftNotReadyError):
        await promote_to_recurring(deps, ctx, params)

    insert_calls = [
        c for c in pool.fetchrow.await_args_list if "insert into luna_judge" in c.args[0]
    ]
    assert insert_calls == []


@pytest.mark.parametrize("status", ["drafting", "backtesting"])
async def test_promote_draft_never_backtested_is_rejected(mocker, status):
    """A draft that has never completed a successful backtest — whether
    it's still fresh (`drafting`) or a backtest was started but hasn't
    finished (`backtesting`) — must be rejected. Only a draft that
    reached READY via a completed `_run_backtest` run may be promoted."""
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    draft = _draft_row(project_id, status=status)
    pool = _make_pool(mocker, fetchrow_rows=[draft])
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token="approved-by-alice")

    with pytest.raises(DraftNotReadyError):
        await promote_to_recurring(deps, ctx, params)

    insert_calls = [
        c for c in pool.fetchrow.await_args_list if "insert into luna_judge" in c.args[0]
    ]
    assert insert_calls == []


# ----- scope --------------------------------------------------------------


async def test_promote_other_project_draft_raises_scope_error_and_inserts_nothing(mocker):
    ctx = _make_ctx(uuid4())
    draft = _draft_row(project_id=uuid4())  # different project
    pool = _make_pool(mocker, fetchrow_rows=[draft])
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=draft["id"], approval_token="approved-by-alice")

    with pytest.raises(ScopeError):
        await promote_to_recurring(deps, ctx, params)

    insert_calls = [
        c for c in pool.fetchrow.await_args_list if "insert into luna_judge" in c.args[0]
    ]
    assert insert_calls == []


async def test_promote_missing_draft_raises_scope_error(mocker):
    ctx = _make_ctx(uuid4())
    pool = _make_pool(mocker, fetchrow_rows=[None])
    deps = _make_deps(pool)
    params = PromoteIn(draft_id=uuid4(), approval_token="approved-by-alice")

    with pytest.raises(ScopeError):
        await promote_to_recurring(deps, ctx, params)
