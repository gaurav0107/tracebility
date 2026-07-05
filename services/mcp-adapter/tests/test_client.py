"""VerbHTTPClient (Task 8, D2-A).

Thin httpx translation layer: one async method per agent-drivable
verb, each POSTing to the matching `/v1/verbs/...` route with the
session cookie, and passing the response JSON straight through. No
``mcp`` import anywhere in this test module — the client is pure
httpx + a domain error, testable without the optional `mcp` SDK.

Mocking strategy: monkeypatch `httpx.AsyncClient` with a fake whose
`post` is an AsyncMock returning a fake response object (`.status_code`
+ `.json()` + `.text`), so no real network call is ever made and we can
assert exactly what URL/json/cookies each verb method sent.
"""

from __future__ import annotations

import httpx
import pytest
from langprobe_mcp_adapter.client import VerbCallError, VerbHTTPClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient capturing the last call's kwargs."""

    instances: list[_FakeAsyncClient] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.calls: list[dict] = []
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, cookies=None, **kwargs):
        self.calls.append({"url": url, "json": json, "cookies": cookies})
        return self._next_response


def _install_fake_client(monkeypatch, response: _FakeResponse):
    _FakeAsyncClient.instances = []
    _FakeAsyncClient._next_response = response
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeAsyncClient.instances = []
    yield


BASE_URL = "http://api.internal:8000"
SESSION_COOKIE = "sess-token-abc123"


def _client() -> VerbHTTPClient:
    return VerbHTTPClient(base_url=BASE_URL, session_cookie=SESSION_COOKIE)


async def test_cluster_failures_posts_to_correct_url_with_payload_and_cookie(monkeypatch):
    _install_fake_client(monkeypatch, _FakeResponse(200, {"clusters": []}))
    client = _client()
    params = {"project_id": "p1", "window_hours": 24, "group_by": "error"}

    out = await client.cluster_failures(params)

    assert out == {"clusters": []}
    call = _FakeAsyncClient.instances[0].calls[0]
    assert call["url"] == f"{BASE_URL}/v1/verbs/cluster-failures"
    assert call["json"] == params
    assert call["cookies"] == {"langprobe_session": SESSION_COOKIE}


async def test_propose_eval_posts_to_correct_url_with_payload_and_cookie(monkeypatch):
    _install_fake_client(monkeypatch, _FakeResponse(200, {"draft_id": "d1", "status": "ready"}))
    client = _client()
    params = {"project_id": "p1", "sample_run_ids": ["r1"], "group_key": "TimeoutError"}

    out = await client.propose_eval(params)

    assert out == {"draft_id": "d1", "status": "ready"}
    call = _FakeAsyncClient.instances[0].calls[0]
    assert call["url"] == f"{BASE_URL}/v1/verbs/propose-eval"
    assert call["json"] == params
    assert call["cookies"] == {"langprobe_session": SESSION_COOKIE}


async def test_run_judge_over_cohort_posts_to_correct_url_with_payload_and_cookie(monkeypatch):
    _install_fake_client(
        monkeypatch, _FakeResponse(202, {"backtest_run_id": "b1", "status": "queued"})
    )
    client = _client()
    params = {"project_id": "p1", "draft_id": "d1", "window_hours": 24}

    out = await client.run_judge_over_cohort(params)

    assert out == {"backtest_run_id": "b1", "status": "queued"}
    call = _FakeAsyncClient.instances[0].calls[0]
    assert call["url"] == f"{BASE_URL}/v1/verbs/backtest"
    assert call["json"] == params
    assert call["cookies"] == {"langprobe_session": SESSION_COOKIE}


async def test_watch_judge_posts_to_correct_url_with_payload_and_cookie(monkeypatch):
    _install_fake_client(
        monkeypatch,
        _FakeResponse(200, {"status": "done", "caught": 2, "missed": 1, "error": None}),
    )
    client = _client()
    params = {"project_id": "p1", "target_id": "b1"}

    out = await client.watch_judge(params)

    assert out == {"status": "done", "caught": 2, "missed": 1, "error": None}
    call = _FakeAsyncClient.instances[0].calls[0]
    assert call["url"] == f"{BASE_URL}/v1/verbs/watch"
    assert call["json"] == params
    assert call["cookies"] == {"langprobe_session": SESSION_COOKIE}


@pytest.mark.parametrize(
    "method_name,params",
    [
        ("cluster_failures", {"project_id": "p1", "window_hours": 24, "group_by": "error"}),
        ("propose_eval", {"project_id": "p1", "sample_run_ids": ["r1"], "group_key": "x"}),
        ("run_judge_over_cohort", {"project_id": "p1", "draft_id": "d1", "window_hours": 24}),
        ("watch_judge", {"project_id": "p1", "target_id": "b1"}),
    ],
)
async def test_non_2xx_response_raises_verb_call_error(monkeypatch, method_name, params):
    _install_fake_client(
        monkeypatch, _FakeResponse(403, {"detail": "out of scope"}, text="forbidden")
    )
    client = _client()
    method = getattr(client, method_name)

    with pytest.raises(VerbCallError) as exc_info:
        await method(params)

    assert exc_info.value.status_code == 403
    assert "forbidden" in str(exc_info.value) or "403" in str(exc_info.value)


async def test_verb_call_error_carries_status_and_body():
    err = VerbCallError(status_code=500, body="boom")
    assert err.status_code == 500
    assert err.body == "boom"
