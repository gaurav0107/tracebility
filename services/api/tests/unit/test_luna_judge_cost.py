"""apply_luna_judge surfaces per-call cost as the 5th return element."""

from __future__ import annotations

import pytest
from langprobe_api.routers.luna_judges import apply_luna_judge

pytestmark = pytest.mark.asyncio


async def test_stub_path_returns_zero_cost(mocker):
    judge_row = {
        "slug": "s",
        "rubric_prompt": "rate {output}",
        "output_format": "score-rationale",
        "provider": "stub",
        "model": "stub",
        "temperature": 0.0,
        "max_tokens": 512,
    }
    out = await apply_luna_judge(
        judge_row,
        pool=mocker.MagicMock(),
        project_id="11111111-1111-1111-1111-111111111111",
        surface="luna",
        surface_ref_id="22222222-2222-2222-2222-222222222222",
        input_text="hi",
        expected="",
        output_text="hello",
    )
    assert len(out) == 5
    score, label, rationale, raw_output, cost_usd = out
    assert cost_usd == 0.0
