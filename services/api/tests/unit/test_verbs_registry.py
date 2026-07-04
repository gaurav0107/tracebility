"""Verb registry (Task 2, D2-A).

The registry is the single source of truth mapping a versioned verb
name (``langprobe.v1.*``) to its callable implementation. A future HTTP
router and the MCP adapter both dispatch through this table instead of
importing ``verbs.service`` functions directly, so the set of exposed
verbs never drifts between the two surfaces.

Verb logic itself is stubbed in this task (Task 1 already landed the
lifecycle enums; later tasks fill in real behavior) — here we only
assert the registry's shape and that every stub raises
``NotImplementedError``.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from langprobe_api.verbs.deps import VerbDeps
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


@pytest.mark.parametrize(
    "name", sorted(EXPECTED_VERB_NAMES - {"langprobe.v1.cluster_failures"})
)
async def test_registry_stub_raises_not_implemented(name):
    fn = VERB_REGISTRY[name]
    ctx = _make_ctx()
    deps = VerbDeps(pool=None, ch=None)

    with pytest.raises(NotImplementedError):
        result = fn(deps, ctx, None)
        if inspect.isawaitable(result):
            await result
