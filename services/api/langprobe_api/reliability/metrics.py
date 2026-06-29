"""Pure eval-reliability metrics over eval_score rows.

Each row is a dict with ``item_key`` (span_id or run_id), ``judge_name``,
``score`` (float), ``outcome`` (str; 'ok' means the judge output parsed). The
read layer maps ClickHouse rows into this shape; the math lives here so it's
unit-tested in isolation.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReliabilityReport:
    total_scores: int
    schema_adherence: float
    schema_adherence_by_judge: dict[str, float] = field(default_factory=dict)
    test_retest_stddev: float | None = None
    repeated_groups: int = 0
    inter_judge_agreement: float | None = None
    items_multi_judge: int = 0


def compute_reliability(
    rows: list[dict[str, Any]], *, threshold: float = 0.5
) -> ReliabilityReport:
    if not rows:
        return ReliabilityReport(total_scores=0, schema_adherence=1.0)

    # --- schema adherence: fraction of outputs that parsed (outcome == 'ok') ---
    ok_total = 0
    per_judge_ok: dict[str, int] = defaultdict(int)
    per_judge_n: dict[str, int] = defaultdict(int)
    for r in rows:
        judge = str(r.get("judge_name") or "")
        is_ok = str(r.get("outcome") or "ok") == "ok"
        per_judge_n[judge] += 1
        if is_ok:
            ok_total += 1
            per_judge_ok[judge] += 1
    schema_adherence = ok_total / len(rows)
    schema_by_judge = {
        j: per_judge_ok[j] / per_judge_n[j] for j in per_judge_n
    }

    # --- test-retest: stddev of score across repeats of the same (item, judge) ---
    by_item_judge: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        key = (str(r.get("item_key") or ""), str(r.get("judge_name") or ""))
        by_item_judge[key].append(float(r.get("score") or 0.0))
    repeated = [scores for scores in by_item_judge.values() if len(scores) > 1]
    if repeated:
        test_retest = statistics.fmean(statistics.pstdev(s) for s in repeated)
    else:
        test_retest = None

    # --- inter-judge agreement: per item, do all judges agree (binarized)? ---
    by_item: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        item = str(r.get("item_key") or "")
        judge = str(r.get("judge_name") or "")
        # last write wins for (item, judge) — agreement is about the verdict,
        # repeats are handled by test-retest above.
        by_item[item][judge] = float(r.get("score") or 0.0)
    multi = [verdicts for verdicts in by_item.values() if len(verdicts) > 1]
    if multi:
        agree = 0
        for verdicts in multi:
            binar = {s >= threshold for s in verdicts.values()}
            if len(binar) == 1:
                agree += 1
        inter_judge = agree / len(multi)
    else:
        inter_judge = None

    return ReliabilityReport(
        total_scores=len(rows),
        schema_adherence=schema_adherence,
        schema_adherence_by_judge=schema_by_judge,
        test_retest_stddev=test_retest,
        repeated_groups=len(repeated),
        inter_judge_agreement=inter_judge,
        items_multi_judge=len(multi),
    )
