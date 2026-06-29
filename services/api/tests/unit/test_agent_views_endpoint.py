"""Route-level wiring test for the agent-view endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langprobe_api.auth import Principal, require_user
from langprobe_api.routers import agent_views

_PROJECT = str(uuid4())
_RUN = str(uuid4())


class _FakePool:
    def __init__(self, workspace_id, role):
        self._workspace_id = workspace_id
        self._role = role

    async def fetchval(self, sql, *args):
        if "from project" in sql:
            return self._workspace_id
        if "workspace_member" in sql:
            return self._role
        return None


class _FakeCH:
    def __init__(self, *, runs=None, run=None, spans=None):
        self._runs = runs or []
        self._run = run
        self._spans = spans or []

    async def query(self, sql, parameters=None):
        if "status = 'error'" in sql:
            return self._runs
        if "from run" in sql:
            return [self._run] if self._run else []
        if "from span" in sql:
            return self._spans
        return []


def _make_app(ch, *, role="viewer"):
    app = FastAPI()
    app.include_router(agent_views.router)
    app.state.pg = _FakePool(workspace_id=uuid4(), role=role)
    app.state.clickhouse = ch
    app.dependency_overrides[require_user] = lambda: Principal(
        user_id=uuid4(), email="t@example.com", is_root=True
    )
    return app


def test_failed_runs_returns_list():
    ch = _FakeCH(runs=[
        {"run_id": "r1", "name": "loop", "error_kind": "X",
         "error_message": "boom", "start_time": "2026-06-29T00:00:00Z"}
    ])
    client = TestClient(_make_app(ch))
    res = client.get(f"/v1/agent/failed-runs?project_id={_PROJECT}")
    assert res.status_code == 200, res.text
    assert res.json()["runs"][0]["run_id"] == "r1"


def test_agent_view_returns_projection():
    ch = _FakeCH(
        run={"run_id": "r1", "name": "loop", "status": "error",
             "kind": "chain", "error_kind": "X", "error_message": "boom"},
        spans=[{"span_id": "s1", "kind": "llm", "status": "ok", "name": "p",
                "inputs": "i", "outputs": "o", "latency_ms": 10}],
    )
    client = TestClient(_make_app(ch))
    res = client.get(f"/v1/runs/{_RUN}/agent-view?project_id={_PROJECT}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["run_id"] == "r1"
    assert "compact_text" in body
    assert body["est_tokens"] <= 2000


def test_agent_view_404_when_run_missing():
    ch = _FakeCH(run=None)
    client = TestClient(_make_app(ch))
    res = client.get(f"/v1/runs/{_RUN}/agent-view?project_id={_PROJECT}")
    assert res.status_code == 404


def test_agent_view_403_without_role():
    ch = _FakeCH(run={"run_id": "r1"}, spans=[])

    app = FastAPI()
    app.include_router(agent_views.router)

    class _NoRolePool:
        async def fetchval(self, sql, *args):
            if "from project" in sql:
                return uuid4()
            return None  # no workspace_member role

    app.state.pg = _NoRolePool()
    app.state.clickhouse = ch
    app.dependency_overrides[require_user] = lambda: Principal(
        user_id=uuid4(), email="t@example.com", is_root=True
    )
    res = TestClient(app).get(f"/v1/runs/{_RUN}/agent-view?project_id={_PROJECT}")
    assert res.status_code == 403
