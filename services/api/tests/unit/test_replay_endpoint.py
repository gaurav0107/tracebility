"""Route-level wiring test for POST /v1/runs/{run_id}/replay.

The replay engine internals (diff / executor / record) are unit-tested directly.
This covers the endpoint's I/O wiring — auth + RBAC, span load, capturable
lookup, dispatch wiring, replay_run persistence, response shape — with a stubbed
Postgres pool and ClickHouse client so it runs without live services.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langprobe_api.auth import Principal, require_user
from langprobe_api.routers import replay_runs

_PROJECT = str(uuid4())
_RUN = str(uuid4())
_SPAN_A = str(uuid4())
_SPAN_B = str(uuid4())


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
    def __init__(self, spans, capturable):
        self._spans = spans
        self._capturable = capturable
        self.inserts: list[tuple] = []

    async def query(self, sql, parameters=None):
        if "from span" in sql:
            return self._spans
        if "replay_capture" in sql:
            return [{"span_id": sid} for sid in self._capturable]
        return []

    async def insert(self, table, rows, column_names=None):
        self.inserts.append((table, rows, column_names))


def _span(span_id, *, kind="llm", outputs="orig"):
    return {
        "span_id": span_id,
        "name": kind,
        "kind": kind,
        "model": "anthropic/claude-sonnet-4-6",
        "temperature": 0.0,
        "inputs": "hello",
        "outputs": outputs,
        "cost_usd": 0.001,
        "latency_ms": 100,
    }


def _make_app(spans, capturable, *, role="owner"):
    app = FastAPI()
    app.include_router(replay_runs.runs_router)
    app.state.pg = _FakePool(workspace_id=uuid4(), role=role)
    ch = _FakeCH(spans, capturable)
    app.state.clickhouse = ch
    app.dependency_overrides[require_user] = lambda: Principal(
        user_id=uuid4(), email="t@example.com", is_root=True
    )
    return app, ch


def test_no_edit_replay_is_ok_zero_divergence_and_persists():
    app, ch = _make_app([_span(_SPAN_A), _span(_SPAN_B)], {_SPAN_A, _SPAN_B})
    client = TestClient(app)
    res = client.post(f"/v1/runs/{_RUN}/replay", json={"project_id": _PROJECT, "edits": []})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "ok"
    assert body["span_count_total"] == 2
    assert body["span_count_diverged"] == 0
    assert body["determinism"] == "deterministic"
    # replay_run row persisted exactly once
    assert [t for (t, _r, _c) in ch.inserts] == ["replay_run"]


def test_missing_capture_edit_is_loud():
    # Edit span B but only A is capturable -> tool_io_missing, no dispatch.
    app, ch = _make_app([_span(_SPAN_A), _span(_SPAN_B)], {_SPAN_A})
    client = TestClient(app)
    res = client.post(
        f"/v1/runs/{_RUN}/replay",
        json={
            "project_id": _PROJECT,
            "edits": [{"target_span_id": _SPAN_B, "field": "prompt", "value": "x"}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "tool_io_missing"
    assert "not fully replayable" in body["summary"].lower()


def test_edited_span_redispatches_via_gateway(monkeypatch):
    app, ch = _make_app([_span(_SPAN_A, outputs="old")], {_SPAN_A})

    class _Result:
        text = "new output"
        prompt_tokens = 10
        completion_tokens = 5

    async def _fake_gateway(pool, **kwargs):
        return _Result()

    monkeypatch.setattr("langprobe_api.llm.dispatch", _fake_gateway)
    monkeypatch.setattr(
        "langprobe_api.routers.playground._resolve_provider",
        lambda model: "anthropic",
    )

    client = TestClient(app)
    res = client.post(
        f"/v1/runs/{_RUN}/replay",
        json={
            "project_id": _PROJECT,
            "edits": [{"target_span_id": _SPAN_A, "field": "prompt", "value": "edited"}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # edited divergence is expected -> outcome ok, one span diverged
    assert body["outcome"] == "ok"
    assert body["span_count_diverged"] == 1
    delta = body["deltas"][0]
    assert delta["output_changed"] is True
    assert delta["diverged"] is True


def test_run_with_no_spans_404():
    app, _ch = _make_app([], set())
    client = TestClient(app)
    res = client.post(f"/v1/runs/{_RUN}/replay", json={"project_id": _PROJECT, "edits": []})
    assert res.status_code == 404


def test_insufficient_role_403():
    app, _ch = _make_app([_span(_SPAN_A)], {_SPAN_A}, role="viewer")
    client = TestClient(app)
    res = client.post(f"/v1/runs/{_RUN}/replay", json={"project_id": _PROJECT, "edits": []})
    assert res.status_code == 403
