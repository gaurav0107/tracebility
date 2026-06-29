"""Eval-reliability metrics — what turns "we have judges" into "our judges are
trustworthy". Pure aggregations over eval_score rows:

- schema_adherence: fraction of judge outputs that parsed cleanly (outcome='ok').
- test_retest: score stability when the same item+judge is scored repeatedly
  (population stddev; lower = more reliable).
- inter_judge_agreement: how often a panel of judges agrees on an item
  (binarized at a threshold).
"""

from __future__ import annotations

from langprobe_api.reliability.metrics import compute_reliability


def _row(item, judge, score, *, outcome="ok"):
    return {"item_key": item, "judge_name": judge, "score": score, "outcome": outcome}


def test_empty_is_safe_zeros():
    r = compute_reliability([])
    assert r.total_scores == 0
    assert r.schema_adherence == 1.0  # vacuously clean
    assert r.test_retest_stddev is None
    assert r.inter_judge_agreement is None


def test_schema_adherence_counts_ok_outcomes():
    rows = [
        _row("i1", "j", 1.0, outcome="ok"),
        _row("i2", "j", 0.0, outcome="schema_violation"),
        _row("i3", "j", 1.0, outcome="ok"),
        _row("i4", "j", 1.0, outcome="ok"),
    ]
    r = compute_reliability(rows)
    assert r.schema_adherence == 0.75
    assert r.schema_adherence_by_judge["j"] == 0.75


def test_test_retest_zero_variance_is_perfectly_stable():
    rows = [_row("i1", "j", 0.8), _row("i1", "j", 0.8), _row("i1", "j", 0.8)]
    r = compute_reliability(rows)
    assert r.test_retest_stddev == 0.0
    assert r.repeated_groups == 1


def test_test_retest_picks_up_instability():
    # same item+judge scored 0.0 then 1.0 -> population stddev 0.5
    rows = [_row("i1", "j", 0.0), _row("i1", "j", 1.0)]
    r = compute_reliability(rows)
    assert abs(r.test_retest_stddev - 0.5) < 1e-9


def test_test_retest_ignores_single_shot_items():
    rows = [_row("i1", "j", 0.5), _row("i2", "j", 0.9)]
    r = compute_reliability(rows)
    assert r.test_retest_stddev is None  # no repeats
    assert r.repeated_groups == 0


def test_inter_judge_agreement_full_when_panel_agrees():
    rows = [
        _row("i1", "a", 0.9), _row("i1", "b", 0.8),  # both pass
        _row("i2", "a", 0.1), _row("i2", "b", 0.2),  # both fail
    ]
    r = compute_reliability(rows, threshold=0.5)
    assert r.inter_judge_agreement == 1.0
    assert r.items_multi_judge == 2


def test_inter_judge_agreement_drops_on_split():
    rows = [
        _row("i1", "a", 0.9), _row("i1", "b", 0.1),  # split
        _row("i2", "a", 0.9), _row("i2", "b", 0.8),  # agree
    ]
    r = compute_reliability(rows, threshold=0.5)
    assert r.inter_judge_agreement == 0.5


def test_inter_judge_agreement_none_with_single_judge():
    rows = [_row("i1", "a", 0.9), _row("i2", "a", 0.1)]
    r = compute_reliability(rows)
    assert r.inter_judge_agreement is None
