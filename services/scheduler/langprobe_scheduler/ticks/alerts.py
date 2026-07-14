"""Alert-evaluator tick.

Reuses the API's evaluation logic verbatim (langprobe_api.alerts.evaluator),
run here on a timer instead of inside the API lifespan. Single-writer safety
across replicas comes from the per-rule advisory lock inside the evaluator.
"""

from __future__ import annotations

from langprobe_api.alerts.evaluator import evaluate_due_rules


async def evaluate_alerts_once(pool, clickhouse) -> None:
    """One alert-evaluator pass over all enabled rules."""
    await evaluate_due_rules(pool, clickhouse)
