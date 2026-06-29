"""Agent-mode projections — token-budgeted, LLM-legible views of a run.

The agent-first wedge: an agent debugging an agent cannot eat a 25k-span raw
trace. `project_run` returns the salient slice within a token budget — errored
spans first, big I/O truncated with a marker, a one-line verdict an agent reads
first. Pure, so it's the tested substrate every MCP tool and agent-view endpoint
wraps.
"""

from __future__ import annotations

from langprobe_api.agent.projections import (
    compact_text,
    estimate_tokens,
    project_run,
    truncate,
)


def _run(**kw):
    base = {
        "run_id": "run-1",
        "name": "agent loop",
        "status": "error",
        "kind": "chain",
        "latency_ms": 1200,
        "cost_usd": 0.02,
        "error_kind": "ToolError",
        "error_message": "search tool 500",
    }
    base.update(kw)
    return base


def _span(span_id, *, kind="llm", status="ok", name=None, inputs="in", outputs="out",
          latency_ms=100):
    return {
        "span_id": span_id,
        "kind": kind,
        "status": status,
        "name": name or kind,
        "inputs": inputs,
        "outputs": outputs,
        "latency_ms": latency_ms,
    }


def test_truncate_marks_and_reports_original_length():
    out = truncate("x" * 100, 20)
    assert len(out) < 100
    assert "truncated" in out
    assert "100" in out  # original length surfaced


def test_truncate_noop_when_within_limit():
    assert truncate("short", 50) == "short"


def test_small_run_fits_all_spans_not_truncated():
    spans = [_span("a"), _span("b")]
    p = project_run(_run(), spans, token_budget=2000)
    assert len(p.spans) == 2
    assert p.truncated is False
    assert p.est_tokens <= 2000


def test_errored_span_surfaces_first_even_under_tight_budget():
    spans = [
        _span("ok1", status="ok", outputs="o" * 400),
        _span("ok2", status="ok", outputs="o" * 400),
        _span("boom", status="error", name="search", outputs="boom" * 100),
    ]
    p = project_run(_run(), spans, token_budget=200)
    assert p.truncated is True
    # the errored span is included despite the tight budget
    assert any(s.span_id == "boom" for s in p.spans)
    # and it's ordered before the ok spans that made the cut
    assert p.spans[0].span_id == "boom"


def test_large_io_is_truncated_per_span():
    spans = [_span("a", outputs="z" * 5000)]
    p = project_run(_run(), spans, token_budget=2000)
    s = p.spans[0]
    assert s.truncated is True
    assert len(s.outputs_preview) < 5000


def test_summary_has_status_error_and_counts():
    spans = [_span("a"), _span("b", status="error")]
    p = project_run(_run(), spans, token_budget=2000)
    assert "error" in p.summary.lower()
    assert "search tool 500" in p.summary  # error message surfaced
    assert "2" in p.summary  # span count


def test_respects_budget_drops_low_salience_spans():
    spans = [_span(f"s{i}", outputs="o" * 300) for i in range(40)]
    p = project_run(_run(status="ok", error_kind="", error_message=""), spans,
                    token_budget=400)
    assert p.truncated is True
    assert len(p.spans) < 40
    assert p.est_tokens <= 400 * 1.2  # soft ceiling, never wildly over


def test_compact_text_is_legible_and_bounded():
    spans = [_span("a"), _span("boom", status="error", name="search")]
    p = project_run(_run(), spans, token_budget=2000)
    text = compact_text(p)
    assert "run-1" in text
    assert "search" in text
    assert estimate_tokens(text) <= 2000


def test_estimate_tokens_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40) >= estimate_tokens("a" * 4)
