"""TOOLS registry (Task 8, D2-A).

The MCP adapter must expose exactly the 4 agent-drivable verbs
(cluster_failures, propose_eval, run_judge_over_cohort, watch_judge)
and MUST NOT expose promote_to_recurring — promoting a draft mutates
production judge config and is a human-only security boundary (see
``routers/verbs.py``'s ``post_promote``). These tests assert the
registry shape without ever importing the ``mcp`` package.
"""

from __future__ import annotations

from langprobe_mcp_adapter.tools import TOOLS

EXPECTED_NAMES = {
    "cluster_failures",
    "propose_eval",
    "run_judge_over_cohort",
    "watch_judge",
}


def test_tools_exposes_exactly_the_four_agent_verbs():
    names = {tool["name"] for tool in TOOLS}
    assert names == EXPECTED_NAMES


def test_tools_does_not_include_promote():
    names = {tool["name"] for tool in TOOLS}
    assert "promote_to_recurring" not in names
    assert "promote" not in names


def test_each_tool_has_name_description_and_input_schema():
    for tool in TOOLS:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema


def test_tools_registry_has_no_duplicate_names():
    names = [tool["name"] for tool in TOOLS]
    assert len(names) == len(set(names))
