"""Agent-first surface — langprobe consumed by agents, not just humans.

Every observability tool today is a dashboard for a human. langprobe's wedge is
agents debugging agents: an agent queries a broken run, forms a hypothesis,
replays an edit, reads the diff, checks the eval delta, iterates.

This package holds the substrate that makes that possible:
- ``projections``: token-budgeted, LLM-legible views (a raw 25k-span trace does
  not fit a context window — the agent gets the salient slice).

Phase 1 also exposes these over MCP (the primary agent interface) and agent-view
HTTP endpoints. The projection layer is shared by both.
"""
