"""Replay engine — the agent debugger wedge.

Phase 0 (here): backend span-level what-if. Re-dispatch the edited span(s)
live, hold the rest at their captured values, and diff. Results land in the
``replay_run`` ClickHouse table (schema 0003).

Phase 2 (roadmap): a client-side SDK harness re-executes real control flow,
serving non-edited calls from capture (content-hash match, 1C). The diff
computation in ``diff.py`` is shared across both phases — it consumes already
-correlated span pairs, so the correlation strategy stays a separate concern.
"""
