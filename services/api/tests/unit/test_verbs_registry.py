"""Verb registry (Task 2, D2-A).

The registry is the single source of truth mapping a versioned verb
name (``langprobe.v1.*``) to its callable implementation. A future HTTP
router and the MCP adapter both dispatch through this table instead of
importing ``verbs.service`` functions directly, so the set of exposed
verbs never drifts between the two surfaces.

Verb logic was filled in incrementally (``cluster_failures`` in Task 3,
``run_judge_over_cohort`` in Task 4, ``propose_eval`` in Task 5,
``promote_to_recurring`` + ``watch_judge`` in Task 6). As of Task 6, all
5 verbs are real — none should raise ``NotImplementedError`` anymore.
This suite only asserts the registry's shape; behavior for each verb is
covered by its own ``test_verb_*.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from langprobe_api.verbs.registry import VERB_REGISTRY
from langprobe_tenant.context import TenantContext

EXPECTED_VERB_NAMES = frozenset(
    {
        "langprobe.v1.cluster_failures",
        "langprobe.v1.propose_eval",
        "langprobe.v1.run_judge_over_cohort",
        "langprobe.v1.promote_to_recurring",
        "langprobe.v1.watch_judge",
    }
)


def _make_ctx() -> TenantContext:
    project_id = uuid4()
    return TenantContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        project_id=project_id,
        api_key_id=uuid4(),
        plan="pro",
        scopes=frozenset({"verbs:*"}),
    )


def test_registry_contains_exactly_the_five_v1_verb_names():
    assert set(VERB_REGISTRY.keys()) == EXPECTED_VERB_NAMES


@pytest.mark.parametrize("name", sorted(EXPECTED_VERB_NAMES))
def test_registry_entries_are_callable(name):
    assert callable(VERB_REGISTRY[name])
