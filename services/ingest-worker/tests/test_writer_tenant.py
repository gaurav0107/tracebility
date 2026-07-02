"""End-to-end: writer materializes envelopes with tenant columns."""

from __future__ import annotations

import json
import socket
from uuid import uuid4

import pytest
from langprobe_worker.writer import (
    _MISSING_TENANT_UUID,
    _REPLAY_CAPTURE_COLUMNS,
    _RUN_COLUMNS,
    _SPAN_COLUMNS,
    ClickHouseWriter,
    _row_for_replay_capture,
    _row_for_run,
    _row_for_span,
)


def _ch_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 8123), timeout=0.25):
            return True
    except OSError:
        return False


def _envelope(*, with_tenant: bool, project_id: str, org_id: str, workspace_id: str) -> dict:
    env = {
        "project_id": project_id,
        "received_at": "2026-06-07T00:00:00Z",
        "payload": {
            "runs": [
                {
                    "run_id": str(uuid4()),
                    "name": "test-run",
                    "kind": "chain",
                    "status": "ok",
                    "start_time": "2026-06-07T00:00:00Z",
                    "spans": [
                        {
                            "span_id": str(uuid4()),
                            "name": "tool-1",
                            "kind": "tool",
                            "status": "ok",
                            "start_time": "2026-06-07T00:00:00Z",
                            "inputs": "in",
                            "outputs": "out",
                        }
                    ],
                }
            ]
        },
    }
    if with_tenant:
        env["org_id"] = org_id
        env["workspace_id"] = workspace_id
    return env


def test_row_columns_have_tenant_prefix() -> None:
    assert _RUN_COLUMNS[:3] == ("org_id", "workspace_id", "project_id")
    assert _SPAN_COLUMNS[:3] == ("org_id", "workspace_id", "project_id")
    assert _REPLAY_CAPTURE_COLUMNS[:3] == ("org_id", "workspace_id", "project_id")


def test_row_for_run_threads_tenant() -> None:
    org = str(uuid4())
    ws = str(uuid4())
    proj = str(uuid4())
    env = _envelope(with_tenant=True, project_id=proj, org_id=org, workspace_id=ws)
    run = env["payload"]["runs"][0]
    row = _row_for_run(env, run)
    assert row[0] == org
    assert row[1] == ws
    assert row[2] == proj


def test_row_for_span_threads_tenant() -> None:
    org = str(uuid4())
    ws = str(uuid4())
    proj = str(uuid4())
    env = _envelope(with_tenant=True, project_id=proj, org_id=org, workspace_id=ws)
    run = env["payload"]["runs"][0]
    span = run["spans"][0]
    row = _row_for_span(env, span, parent_run_id=run["run_id"])
    assert row[0] == org
    assert row[1] == ws
    assert row[2] == proj


def test_row_for_replay_capture_threads_tenant() -> None:
    org = str(uuid4())
    ws = str(uuid4())
    proj = str(uuid4())
    env = _envelope(with_tenant=True, project_id=proj, org_id=org, workspace_id=ws)
    run = env["payload"]["runs"][0]
    span = run["spans"][0]
    row = _row_for_replay_capture(env, span, parent_run_id=run["run_id"])
    assert row is not None
    assert row[0] == org
    assert row[1] == ws
    assert row[2] == proj


def test_row_for_run_round_trips_end_user() -> None:
    """end_user_id / end_user_metadata land in their _RUN_COLUMNS slots."""
    proj = str(uuid4())
    env = _envelope(
        with_tenant=True, project_id=proj, org_id=str(uuid4()), workspace_id=str(uuid4())
    )
    run = env["payload"]["runs"][0]
    run["end_user_id"] = "customer-42"
    run["end_user_metadata"] = {"plan": "pro", "region": "us"}
    row = _row_for_run(env, run)

    eu_idx = _RUN_COLUMNS.index("end_user_id")
    eum_idx = _RUN_COLUMNS.index("end_user_metadata")
    # Must sit immediately after user_id (mirrors the ClickHouse `after user_id`).
    assert eu_idx == _RUN_COLUMNS.index("user_id") + 1
    assert eum_idx == eu_idx + 1
    assert row[eu_idx] == "customer-42"
    assert json.loads(row[eum_idx]) == {"plan": "pro", "region": "us"}


def test_row_for_run_end_user_defaults() -> None:
    """Absent end-user fields serialize to NULL id + empty-dict json string."""
    proj = str(uuid4())
    env = _envelope(
        with_tenant=True, project_id=proj, org_id=str(uuid4()), workspace_id=str(uuid4())
    )
    run = env["payload"]["runs"][0]
    row = _row_for_run(env, run)

    eu_idx = _RUN_COLUMNS.index("end_user_id")
    eum_idx = _RUN_COLUMNS.index("end_user_metadata")
    assert row[eu_idx] is None
    assert row[eum_idx] == "{}"


def test_legacy_envelope_uses_sentinel(caplog) -> None:
    """Pre-Phase-5 envelope (no org/workspace) writes the sentinel UUID
    and emits a structured warning."""
    proj = str(uuid4())
    env = _envelope(with_tenant=False, project_id=proj, org_id="unused", workspace_id="unused")
    run = env["payload"]["runs"][0]
    row = _row_for_run(env, run)
    assert row[0] == _MISSING_TENANT_UUID
    assert row[1] == _MISSING_TENANT_UUID
    assert row[2] == proj


@pytest.mark.skipif(not _ch_reachable(), reason="clickhouse not reachable on localhost:8123")
def test_writer_persists_to_clickhouse() -> None:
    """Smoke test: writer.insert_envelope -> we can SELECT the row back
    out, and the row has org_id + workspace_id."""
    org = str(uuid4())
    ws = str(uuid4())
    proj = str(uuid4())
    env = _envelope(with_tenant=True, project_id=proj, org_id=org, workspace_id=ws)
    run_id = env["payload"]["runs"][0]["run_id"]

    writer = ClickHouseWriter("http://langprobe:langprobe@localhost:8123/langprobe")
    runs, spans, captures = writer.insert_envelope(env)
    assert runs == 1
    assert spans == 1
    assert captures == 1

    rows = writer._client.query(
        "select org_id, workspace_id, project_id from run where run_id = %(run_id)s",
        parameters={"run_id": run_id},
    ).result_rows
    assert len(rows) == 1
    assert str(rows[0][0]) == org
    assert str(rows[0][1]) == ws
    assert str(rows[0][2]) == proj
    writer.close()
