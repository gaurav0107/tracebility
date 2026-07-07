"""_measure computes judge_score_avg from eval_score for the bound judge."""

from __future__ import annotations

import pytest
from langprobe_api.alerts.evaluator import _measure

pytestmark = pytest.mark.asyncio


class _FakeCH:
    def __init__(self, rows):
        self._rows = rows
        self.last_params = None

    async def query(self, sql, parameters=None):
        self.last_params = parameters
        assert "eval_score" in sql
        return self._rows


async def test_judge_score_avg_reads_eval_score_for_subject():
    subject = "33333333-3333-3333-3333-333333333333"
    ch = _FakeCH([{"avg_score": 0.4}])
    rule = {
        "metric": "judge_score_avg",
        "project_id": "11111111-1111-1111-1111-111111111111",
        "window_seconds": 300,
        "subject_id": subject,
    }
    value = await _measure(ch, rule)
    assert value == 0.4
    assert ch.last_params["subject_id"] == str(subject)


async def test_judge_score_avg_no_rows_is_none():
    ch = _FakeCH([])
    rule = {
        "metric": "judge_score_avg",
        "project_id": "11111111-1111-1111-1111-111111111111",
        "window_seconds": 300,
        "subject_id": "33333333-3333-3333-3333-333333333333",
    }
    assert await _measure(ch, rule) is None
