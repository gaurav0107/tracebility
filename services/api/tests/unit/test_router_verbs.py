"""HTTP router for the 5 agent-native eval-loop verbs (Task 7, D2-A).

The verb service layer (``verbs/service.py``) is framework-agnostic —
these tests check the THIN wiring layer on top of it:

- every route requires a session principal (``require_user``);
- the acting ``project_id`` is scope-checked via
  ``tenant_scope.resolve_project_scope`` before the verb runs;
- the ``TenantContext`` handed to the verb carries the resolved scope's
  org_id/workspace_id/project_id, with ``api_key_id`` doubling as the
  acting user's id for session calls;
- domain exceptions raised by the verb are mapped to the right HTTP
  status codes;
- ``POST /v1/verbs/backtest`` returns 202 and schedules the executor
  (``_run_backtest``) as a background task rather than awaiting it
  inline.

The verb functions themselves (``cluster_failures``, ``propose_eval``,
etc.) are monkeypatched here — their internals are already covered by
``test_verb_*.py``. These tests are pure wiring/auth/scope/exception-
mapping/background-scheduling checks.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langprobe_api.auth import Principal, require_user
from langprobe_api.config import Settings
from langprobe_api.llm.types import DispatchError
from langprobe_api.routers import verbs
from langprobe_api.verbs.lifecycle import BacktestStatus, DraftStatus
from langprobe_api.verbs.models import (
    BacktestOut,
    Cluster,
    ClusterFailuresOut,
    EvalDraftOut,
    PromoteOut,
    WatchOut,
)
from langprobe_api.verbs.promote import ApprovalRequiredError, DraftNotReadyError
from langprobe_api.verbs.propose import ProposerFailedError
from langprobe_api.verbs.scope import ScopeError


def _make_app(mocker, *, principal: Principal | None = None):
    """Build a minimal app exercising only the verbs router. ``app.state.pg``
    / ``.clickhouse`` are mocks; ``require_user`` is overridden with a fake
    principal unless the caller wants the real (rejecting) dependency to
    run, in which case pass ``principal=None`` and do not override."""
    app = FastAPI()
    app.include_router(verbs.router)
    app.state.pg = mocker.MagicMock(name="pool")
    app.state.clickhouse = mocker.MagicMock(name="clickhouse")
    # require_user reads settings before it ever inspects the (absent)
    # session cookie — needed even on the "no override" (401) path.
    app.state.settings = Settings(postgres_dsn="postgres://test/test", session_secret="x" * 40)

    if principal is not None:
        app.dependency_overrides[require_user] = lambda: principal
    return app


def _principal() -> Principal:
    return Principal(user_id=uuid4(), email="alice@example.com", is_root=False)


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def fake_scope(mocker, project_id):
    """Patch resolve_project_scope to succeed and return a scope tied to
    project_id, capturing call args for assertion."""
    from langprobe_api.tenant_scope import TenantScope

    org_id = uuid4()
    workspace_id = uuid4()
    scope = TenantScope(
        org_id=org_id, workspace_id=workspace_id, project_id=project_id, principal=_principal()
    )
    mock = mocker.patch(
        "langprobe_api.routers.verbs.resolve_project_scope",
        mocker.AsyncMock(return_value=scope),
    )
    return mock, scope


# ----- cluster-failures -----------------------------------------------------


def test_cluster_failures_returns_200_and_verb_output(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = ClusterFailuresOut(
        clusters=[Cluster(key="TimeoutError", count=3, sample_run_ids=[uuid4()])]
    )
    mocker.patch("langprobe_api.routers.verbs.cluster_failures", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )

    assert resp.status_code == 200
    assert resp.json() == out.model_dump(mode="json")


def test_cluster_failures_requires_auth(mocker, project_id):
    app = _make_app(mocker, principal=None)  # no override: real require_user runs, 401s
    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )
    assert resp.status_code == 401


def test_cluster_failures_invokes_resolve_project_scope_with_project_and_principal(
    mocker, project_id, fake_scope
):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = ClusterFailuresOut(clusters=[])
    mocker.patch("langprobe_api.routers.verbs.cluster_failures", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    _pool, called_project_id, called_principal = mock.await_args.args
    assert called_project_id == project_id
    assert called_principal == principal


def test_cluster_failures_tenant_context_carries_scope_and_principal_id(
    mocker, project_id, fake_scope
):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    _mock, scope = fake_scope
    out = ClusterFailuresOut(clusters=[])
    verb_mock = mocker.patch(
        "langprobe_api.routers.verbs.cluster_failures", mocker.AsyncMock(return_value=out)
    )

    client = TestClient(app)
    client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )

    verb_mock.assert_awaited_once()
    _deps, ctx, _params = verb_mock.await_args.args
    assert ctx.org_id == scope.org_id
    assert ctx.workspace_id == scope.workspace_id
    assert ctx.project_id == scope.project_id
    assert ctx.api_key_id == principal.user_id


@pytest.mark.parametrize(
    "exc,expected_status",
    [
        (ScopeError("nope"), 403),
        (DispatchError("provider_error", "anthropic", "boom"), 503),
    ],
)
def test_cluster_failures_exception_mapping(mocker, project_id, fake_scope, exc, expected_status):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch("langprobe_api.routers.verbs.cluster_failures", mocker.AsyncMock(side_effect=exc))

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )
    assert resp.status_code == expected_status


def test_cluster_failures_allows_viewer_role(mocker, project_id, fake_scope):
    """Read-only triage — allowed_roles must be the 4-role default, viewer included."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = ClusterFailuresOut(clusters=[])
    mocker.patch("langprobe_api.routers.verbs.cluster_failures", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    client.post(
        "/v1/verbs/cluster-failures",
        json={"project_id": str(project_id), "window_hours": 24, "group_by": "error"},
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["allowed_roles"] == ("owner", "admin", "member", "viewer")


# ----- propose-eval ----------------------------------------------------------


def test_propose_eval_returns_200_and_verb_output(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    draft_id = uuid4()
    out = EvalDraftOut(
        draft_id=draft_id,
        judge_kind="luna:proposed",
        judge_config={"prompt": "x", "threshold": 0.5, "label": "fail"},
        status=DraftStatus.READY,
    )
    mocker.patch("langprobe_api.routers.verbs.propose_eval", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/propose-eval",
        json={
            "project_id": str(project_id),
            "sample_run_ids": [str(uuid4())],
            "group_key": "TimeoutError",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["draft_id"] == str(draft_id)


def test_propose_eval_requires_auth(mocker, project_id):
    app = _make_app(mocker, principal=None)
    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/propose-eval",
        json={
            "project_id": str(project_id),
            "sample_run_ids": [str(uuid4())],
            "group_key": "k",
        },
    )
    assert resp.status_code == 401


def test_propose_eval_proposer_failed_maps_to_422(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.propose_eval",
        mocker.AsyncMock(side_effect=ProposerFailedError("bad json twice")),
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/propose-eval",
        json={
            "project_id": str(project_id),
            "sample_run_ids": [str(uuid4())],
            "group_key": "k",
        },
    )
    assert resp.status_code == 422


def test_propose_eval_requires_member_or_above_role(mocker, project_id, fake_scope):
    """Spends LLM money — allowed_roles must exclude viewer."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    draft_id = uuid4()
    out = EvalDraftOut(
        draft_id=draft_id,
        judge_kind="luna:proposed",
        judge_config={"prompt": "x", "threshold": 0.5, "label": "fail"},
        status=DraftStatus.READY,
    )
    mocker.patch("langprobe_api.routers.verbs.propose_eval", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    client.post(
        "/v1/verbs/propose-eval",
        json={
            "project_id": str(project_id),
            "sample_run_ids": [str(uuid4())],
            "group_key": "TimeoutError",
        },
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["allowed_roles"] == ("owner", "admin", "member")


def test_propose_eval_viewer_is_blocked_with_403(mocker, project_id):
    """Simulates a viewer: resolve_project_scope raises 403 when the role gate
    rejects them, and that must surface as a 403 response."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.resolve_project_scope",
        mocker.AsyncMock(side_effect=HTTPException(403, "insufficient role")),
    )
    propose_mock = mocker.patch("langprobe_api.routers.verbs.propose_eval", mocker.AsyncMock())

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/propose-eval",
        json={
            "project_id": str(project_id),
            "sample_run_ids": [str(uuid4())],
            "group_key": "k",
        },
    )

    assert resp.status_code == 403
    propose_mock.assert_not_awaited()


# ----- backtest (async, 202) ------------------------------------------------


def test_backtest_returns_202_and_schedules_background_task(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    backtest_run_id = uuid4()
    out = BacktestOut(backtest_run_id=backtest_run_id, status=BacktestStatus.QUEUED)
    verb_mock = mocker.patch(
        "langprobe_api.routers.verbs.run_judge_over_cohort", mocker.AsyncMock(return_value=out)
    )
    bg_add_task = mocker.patch("fastapi.BackgroundTasks.add_task")

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )

    assert resp.status_code == 202
    assert resp.json()["backtest_run_id"] == str(backtest_run_id)
    verb_mock.assert_awaited_once()
    bg_add_task.assert_called_once()
    # scheduled callable is the framework-agnostic executor, called with
    # (deps, backtest_run_id) — BackgroundTasks itself must never leak
    # into the verb layer.
    scheduled_args = bg_add_task.call_args.args
    assert scheduled_args[0].__name__ == "_run_backtest"
    assert backtest_run_id in scheduled_args


def test_backtest_requires_auth(mocker, project_id):
    app = _make_app(mocker, principal=None)
    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )
    assert resp.status_code == 401


def test_backtest_scope_error_maps_to_403_and_no_background_task(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.run_judge_over_cohort",
        mocker.AsyncMock(side_effect=ScopeError("nope")),
    )
    bg_add_task = mocker.patch("fastapi.BackgroundTasks.add_task")

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )

    assert resp.status_code == 403
    bg_add_task.assert_not_called()


def test_backtest_tenant_context_carries_scope_and_principal_id(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    _mock, scope = fake_scope
    out = BacktestOut(backtest_run_id=uuid4(), status=BacktestStatus.QUEUED)
    verb_mock = mocker.patch(
        "langprobe_api.routers.verbs.run_judge_over_cohort", mocker.AsyncMock(return_value=out)
    )
    mocker.patch("fastapi.BackgroundTasks.add_task")

    client = TestClient(app)
    client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )

    verb_mock.assert_awaited_once()
    _deps, ctx, _params = verb_mock.await_args.args
    assert ctx.org_id == scope.org_id
    assert ctx.project_id == scope.project_id
    assert ctx.api_key_id == principal.user_id


def test_backtest_requires_member_or_above_role(mocker, project_id, fake_scope):
    """Dispatches N judge calls — allowed_roles must exclude viewer."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = BacktestOut(backtest_run_id=uuid4(), status=BacktestStatus.QUEUED)
    mocker.patch(
        "langprobe_api.routers.verbs.run_judge_over_cohort", mocker.AsyncMock(return_value=out)
    )
    mocker.patch("fastapi.BackgroundTasks.add_task")

    client = TestClient(app)
    client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["allowed_roles"] == ("owner", "admin", "member")


def test_backtest_viewer_is_blocked_with_403(mocker, project_id):
    """Simulates a viewer: resolve_project_scope raises 403 when the role gate
    rejects them, and that must surface as a 403 response with no background
    task scheduled."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.resolve_project_scope",
        mocker.AsyncMock(side_effect=HTTPException(403, "insufficient role")),
    )
    verb_mock = mocker.patch(
        "langprobe_api.routers.verbs.run_judge_over_cohort", mocker.AsyncMock()
    )
    bg_add_task = mocker.patch("fastapi.BackgroundTasks.add_task")

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/backtest",
        json={"project_id": str(project_id), "draft_id": str(uuid4()), "window_hours": 24},
    )

    assert resp.status_code == 403
    verb_mock.assert_not_awaited()
    bg_add_task.assert_not_called()


# ----- promote (human-session-gated) ----------------------------------------


def test_promote_returns_200_and_verb_output(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    judge_id = uuid4()
    out = PromoteOut(judge_id=judge_id)
    mocker.patch(
        "langprobe_api.routers.verbs.promote_to_recurring", mocker.AsyncMock(return_value=out)
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["judge_id"] == str(judge_id)


def test_promote_requires_auth(mocker, project_id):
    app = _make_app(mocker, principal=None)
    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "exc,expected_status",
    [
        (ScopeError("nope"), 403),
        (ApprovalRequiredError("blank token"), 403),
        (DraftNotReadyError("not ready"), 409),
    ],
)
def test_promote_exception_mapping(mocker, project_id, fake_scope, exc, expected_status):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.promote_to_recurring", mocker.AsyncMock(side_effect=exc)
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )
    assert resp.status_code == expected_status


def test_promote_tenant_context_carries_scope_and_principal_id(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    _mock, scope = fake_scope
    out = PromoteOut(judge_id=uuid4())
    verb_mock = mocker.patch(
        "langprobe_api.routers.verbs.promote_to_recurring", mocker.AsyncMock(return_value=out)
    )

    client = TestClient(app)
    client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )

    verb_mock.assert_awaited_once()
    _deps, ctx, _params = verb_mock.await_args.args
    assert ctx.org_id == scope.org_id
    assert ctx.project_id == scope.project_id
    assert ctx.api_key_id == principal.user_id


def test_promote_requires_admin_or_above_role(mocker, project_id, fake_scope):
    """Creates a recurring production judge — the human-approval choke point —
    so allowed_roles must exclude both viewer AND member."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = PromoteOut(judge_id=uuid4())
    mocker.patch(
        "langprobe_api.routers.verbs.promote_to_recurring", mocker.AsyncMock(return_value=out)
    )

    client = TestClient(app)
    client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["allowed_roles"] == ("owner", "admin")


def test_promote_viewer_is_blocked_with_403(mocker, project_id):
    """Simulates a viewer (or member): resolve_project_scope raises 403 when
    the role gate rejects them, and that must surface as a 403 response."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.resolve_project_scope",
        mocker.AsyncMock(side_effect=HTTPException(403, "insufficient role")),
    )
    promote_mock = mocker.patch(
        "langprobe_api.routers.verbs.promote_to_recurring", mocker.AsyncMock()
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/promote",
        json={
            "project_id": str(project_id),
            "draft_id": str(uuid4()),
            "approval_token": "approved-by-alice",
        },
    )

    assert resp.status_code == 403
    promote_mock.assert_not_awaited()


# ----- watch -----------------------------------------------------------------


def test_watch_returns_200_and_verb_output(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = WatchOut(status="running", caught=None, missed=None, error=None)
    mocker.patch("langprobe_api.routers.verbs.watch_judge", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/watch",
        json={"project_id": str(project_id), "target_id": str(uuid4())},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_watch_requires_auth(mocker, project_id):
    app = _make_app(mocker, principal=None)
    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/watch",
        json={"project_id": str(project_id), "target_id": str(uuid4())},
    )
    assert resp.status_code == 401


def test_watch_scope_error_maps_to_403(mocker, project_id, fake_scope):
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    mocker.patch(
        "langprobe_api.routers.verbs.watch_judge", mocker.AsyncMock(side_effect=ScopeError("nope"))
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/verbs/watch",
        json={"project_id": str(project_id), "target_id": str(uuid4())},
    )
    assert resp.status_code == 403


def test_watch_allows_viewer_role(mocker, project_id, fake_scope):
    """Read-only status poll — allowed_roles must be the 4-role default, viewer included."""
    principal = _principal()
    app = _make_app(mocker, principal=principal)
    out = WatchOut(status="running", caught=None, missed=None, error=None)
    mocker.patch("langprobe_api.routers.verbs.watch_judge", mocker.AsyncMock(return_value=out))

    client = TestClient(app)
    client.post(
        "/v1/verbs/watch",
        json={"project_id": str(project_id), "target_id": str(uuid4())},
    )

    mock, _scope = fake_scope
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["allowed_roles"] == ("owner", "admin", "member", "viewer")


# ----- app registration -------------------------------------------------------


def test_verbs_router_is_registered_in_create_app():
    from langprobe_api.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/v1/verbs/cluster-failures" in paths
    assert "/v1/verbs/propose-eval" in paths
    assert "/v1/verbs/backtest" in paths
    assert "/v1/verbs/promote" in paths
    assert "/v1/verbs/watch" in paths
