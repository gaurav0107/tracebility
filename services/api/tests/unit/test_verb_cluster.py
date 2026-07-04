"""cluster_failures verb (Task 3, D2-A).

Real logic lives in ``verbs/cluster.py``; ``verbs/service.py`` delegates
to it. These tests mock the ClickHouse client (``deps.ch``) so we never
touch a real database — only assert the verb's own logic: scope
enforcement, window clamping, the fixed group-by column map, and row
mapping into ``Cluster`` models.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langprobe_api.verbs.cluster import GROUP_BY_COLUMNS, MAX_CLUSTERS, MAX_WINDOW_HOURS
from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import ClusterFailuresIn
from langprobe_api.verbs.scope import ScopeError
from langprobe_api.verbs.service import cluster_failures
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


def _make_deps(mocker, rows):
    ch = mocker.MagicMock(name="ch")
    ch.query = mocker.AsyncMock(return_value=rows)
    return VerbDeps(pool=None, ch=ch)


async def test_cluster_failures_maps_rows_to_clusters(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    run_id_1, run_id_2 = uuid4(), uuid4()
    rows = [
        {"key": "TimeoutError", "count": 12, "sample_run_ids": [run_id_1, run_id_2]},
        {"key": "ValueError", "count": 3, "sample_run_ids": [run_id_1]},
    ]
    deps = _make_deps(mocker, rows)
    params = ClusterFailuresIn(project_id=project_id, window_hours=24, group_by="error")

    out = await cluster_failures(deps, ctx, params)

    assert [c.key for c in out.clusters] == ["TimeoutError", "ValueError"]
    assert [c.count for c in out.clusters] == [12, 3]
    assert out.clusters[0].sample_run_ids == [run_id_1, run_id_2]
    assert out.clusters[1].sample_run_ids == [run_id_1]


async def test_cluster_failures_empty_rows_returns_empty_list(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    deps = _make_deps(mocker, [])
    params = ClusterFailuresIn(project_id=project_id, window_hours=24, group_by="tool")

    out = await cluster_failures(deps, ctx, params)

    assert out.clusters == []


async def test_cluster_failures_clamps_window_to_max(mocker):
    project_id = uuid4()
    ctx = _make_ctx(project_id)
    deps = _make_deps(mocker, [])
    params = ClusterFailuresIn(project_id=project_id, window_hours=99999, group_by="status")

    before = datetime.now(UTC)
    await cluster_failures(deps, ctx, params)
    after = datetime.now(UTC)

    assert deps.ch.query.await_count == 1
    _, kwargs = deps.ch.query.await_args
    since = kwargs["parameters"]["since"]
    since_dt = since if isinstance(since, datetime) else datetime.fromisoformat(since)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=UTC)

    expected_earliest = before - timedelta(hours=MAX_WINDOW_HOURS)
    expected_latest = after - timedelta(hours=MAX_WINDOW_HOURS)
    assert expected_earliest <= since_dt <= expected_latest

    assert kwargs["parameters"]["limit"] == MAX_CLUSTERS


async def test_cluster_failures_scope_mismatch_raises_and_skips_query(mocker):
    ctx = _make_ctx(uuid4())
    other_project_id = uuid4()
    deps = _make_deps(mocker, [])
    params = ClusterFailuresIn(project_id=other_project_id, window_hours=24, group_by="error")

    with pytest.raises(ScopeError):
        await cluster_failures(deps, ctx, params)

    deps.ch.query.assert_not_awaited()


def test_group_by_column_map_has_exactly_three_keys():
    assert GROUP_BY_COLUMNS == {
        "error": "error_kind",
        "tool": "name",
        "status": "status",
    }
