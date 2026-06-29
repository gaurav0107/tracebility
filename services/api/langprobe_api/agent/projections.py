"""Token-budgeted, LLM-legible projections of a run.

An agent debugging an agent gets a context window, not a database. `project_run`
returns the salient slice within a token budget: errored spans first, large I/O
truncated with a marker, a one-line verdict. Pure — the tested substrate the MCP
tools and agent-view endpoints both wrap.

Budgeting is deliberately simple (chars/4 token estimate, per-span preview cap).
Errored spans are force-included even when the budget is spent — the whole point
of the view is "what broke," so the break is never the thing we drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_CHARS_PER_TOKEN = 4
_PER_SPAN_PREVIEW_CHARS = 240

# Salience: errored spans first, then by kind (llm carries the reasoning, tool
# the side effects, retriever the context), then slowest first.
_KIND_RANK = {"llm": 0, "tool": 1, "retriever": 2, "chain": 3}


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…[truncated, {len(text)} chars total]"


@dataclass(frozen=True)
class ProjectedSpan:
    span_id: str
    kind: str
    name: str
    status: str
    latency_ms: int
    inputs_preview: str
    outputs_preview: str
    truncated: bool


@dataclass(frozen=True)
class ProjectedRun:
    run_id: str
    name: str
    status: str
    error: str
    summary: str
    spans: list[ProjectedSpan] = field(default_factory=list)
    truncated: bool = False
    est_tokens: int = 0


def _salience(span: dict) -> tuple[int, int, int]:
    is_err = 0 if (span.get("status") or "") == "error" else 1
    kind_rank = _KIND_RANK.get((span.get("kind") or "").lower(), 4)
    return (is_err, kind_rank, -int(span.get("latency_ms") or 0))


def _project_span(span: dict) -> ProjectedSpan:
    inputs = str(span.get("inputs") or "")
    outputs = str(span.get("outputs") or "")
    return ProjectedSpan(
        span_id=str(span.get("span_id") or ""),
        kind=str(span.get("kind") or ""),
        name=str(span.get("name") or ""),
        status=str(span.get("status") or ""),
        latency_ms=int(span.get("latency_ms") or 0),
        inputs_preview=truncate(inputs, _PER_SPAN_PREVIEW_CHARS),
        outputs_preview=truncate(outputs, _PER_SPAN_PREVIEW_CHARS),
        truncated=len(inputs) > _PER_SPAN_PREVIEW_CHARS
        or len(outputs) > _PER_SPAN_PREVIEW_CHARS,
    )


def _span_text(s: ProjectedSpan) -> str:
    return (
        f"  [{s.status}] {s.kind} {s.name} ({s.span_id}) "
        f"{s.latency_ms}ms in={s.inputs_preview} out={s.outputs_preview}"
    )


def _header_text(run_id: str, name: str, status: str, summary: str) -> str:
    return f'run {run_id} "{name}" [{status}] {summary}'


def _summary(run: dict, span_count: int) -> str:
    status = str(run.get("status") or "")
    err_kind = str(run.get("error_kind") or "")
    err_msg = str(run.get("error_message") or "")
    parts = [f"{status} · {span_count} spans"]
    if err_msg or err_kind:
        parts.append(f"{err_kind or 'error'}: {err_msg}".strip())
    return " · ".join(parts)


def project_run(
    run: dict, spans: list[dict], *, token_budget: int = 2000
) -> ProjectedRun:
    """Project a run + its spans into a token-budgeted, agent-legible view."""
    run_id = str(run.get("run_id") or "")
    name = str(run.get("name") or "")
    status = str(run.get("status") or "")
    error = str(run.get("error_message") or "")
    summary = _summary(run, len(spans))

    header_tokens = estimate_tokens(_header_text(run_id, name, status, summary))
    budget_left = token_budget - header_tokens

    ordered = sorted(spans, key=_salience)
    included: list[ProjectedSpan] = []
    truncated = False
    for span in ordered:
        pspan = _project_span(span)
        cost = estimate_tokens(_span_text(pspan))
        is_err = pspan.status == "error"
        if is_err or cost <= budget_left:
            included.append(pspan)
            budget_left -= cost
        else:
            truncated = True
    if len(included) < len(spans):
        truncated = True

    projected = ProjectedRun(
        run_id=run_id,
        name=name,
        status=status,
        error=error,
        summary=summary,
        spans=included,
        truncated=truncated,
    )
    return ProjectedRun(
        run_id=projected.run_id,
        name=projected.name,
        status=projected.status,
        error=projected.error,
        summary=projected.summary,
        spans=projected.spans,
        truncated=projected.truncated,
        est_tokens=estimate_tokens(compact_text(projected)),
    )


def compact_text(p: ProjectedRun) -> str:
    """Render a projection as the compact text an agent reads first."""
    lines = [_header_text(p.run_id, p.name, p.status, p.summary)]
    lines.extend(_span_text(s) for s in p.spans)
    if p.truncated:
        lines.append("  …[spans truncated to fit token budget]")
    return "\n".join(lines)
