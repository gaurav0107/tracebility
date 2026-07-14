"""create_rule enforces the metric<->subject_id biconditional."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from langprobe_api.routers.alerts import AlertRuleCreate, _validate_subject

pytestmark = pytest.mark.asyncio


def test_judge_metric_requires_subject():
    body = AlertRuleCreate(
        project_id=uuid.uuid4(),
        name="w",
        metric="judge_score_avg",
        comparator="<",
        threshold=0.5,
        window_seconds=300,
        subject_id=None,
    )
    with pytest.raises(HTTPException) as ei:
        _validate_subject(body.metric, body.subject_id)
    assert ei.value.status_code == 400


def test_run_metric_rejects_subject():
    with pytest.raises(HTTPException) as ei:
        _validate_subject("error_rate", uuid.uuid4())
    assert ei.value.status_code == 400


def test_judge_metric_with_subject_ok():
    _validate_subject("judge_score_avg", uuid.uuid4())  # no raise
