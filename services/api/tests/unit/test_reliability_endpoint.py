"""Route-level wiring test for GET /v1/eval-reliability."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langprobe_api.auth import Principal, require_user
from langprobe_api.routers import reliability

_PROJECT = str(uuid4())
_CONFIG = str(uuid4())


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
    def __init__(self, rows):
        self._rows = rows

    async def query(self, sql, parameters=None):
        return self._rows


def _make_app(rows, *, role="viewer"):
    app = FastAPI()
    app.include_router(reliability.router)
    app.state.pg = _FakePool(workspace_id=uuid4(), role=role)
    app.state.clickhouse = _FakeCH(rows)
    app.dependency_overrides[require_user] = lambda: Principal(
        user_id=uuid4(), email="t@example.com", is_root=True
    )
    return app


def test_reliability_report_shape():
    rows = [
        {"item_key": "i1", "judge_name": "a", "score": 0.9, "outcome": "ok"},
        {"item_key": "i1", "judge_name": "b", "score": 0.8, "outcome": "ok"},
        {"item_key": "i2", "judge_name": "a", "score": 0.1, "outcome": "schema_violation"},
        {"item_key": "i2", "judge_name": "b", "score": 0.2, "outcome": "ok"},
    ]
    client = TestClient(_make_app(rows))
    res = client.get(
        f"/v1/eval-reliability?project_id={_PROJECT}&eval_config_id={_CONFIG}"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_scores"] == 4
    assert 0.0 <= body["schema_adherence"] <= 1.0
    assert "inter_judge_agreement" in body
    assert body["items_multi_judge"] == 2


def test_reliability_403_without_role():
    app = FastAPI()
    app.include_router(reliability.router)

    class _NoRolePool:
        async def fetchval(self, sql, *args):
            return uuid4() if "from project" in sql else None

    app.state.pg = _NoRolePool()
    app.state.clickhouse = _FakeCH([])
    app.dependency_overrides[require_user] = lambda: Principal(
        user_id=uuid4(), email="t@example.com", is_root=True
    )
    res = TestClient(app).get(
        f"/v1/eval-reliability?project_id={_PROJECT}&eval_config_id={_CONFIG}"
    )
    assert res.status_code == 403
