"""Verb registry (Task 2, D2-A).

Maps each versioned verb name (``langprobe.v1.*``) to its callable
implementation in ``verbs/service.py``. This is the single dispatch
table a future HTTP router and the MCP adapter both read from, so the
set of exposed verbs — and their names — never drifts between the two
surfaces.

Verb logic is stubbed in this task; see ``verbs/service.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs import service
from langprobe_api.verbs.deps import VerbDeps

VERB_REGISTRY: dict[str, Callable[[VerbDeps, TenantContext, Any], Awaitable[Any]]] = {
    "langprobe.v1.cluster_failures": service.cluster_failures,
    "langprobe.v1.propose_eval": service.propose_eval,
    "langprobe.v1.run_judge_over_cohort": service.run_judge_over_cohort,
    "langprobe.v1.promote_to_recurring": service.promote_to_recurring,
    "langprobe.v1.watch_judge": service.watch_judge,
}
