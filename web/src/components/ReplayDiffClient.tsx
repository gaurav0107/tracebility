"use client";

import { useState, useTransition } from "react";

/**
 * Replay & diff — the "real debugger" payoff, client-side.
 *
 * Pick an llm span, edit prompt / model / temperature, run a Phase 0 span-level
 * replay (re-dispatch the edit live, hold the rest at captured values), and read
 * the diff: per-span deltas + the determinism/outcome verdict the backend
 * persists to replay_run. The `summary` line is the same agent-legible string
 * the Phase 1 MCP tool returns — humans and agents read the same verdict.
 *
 * Only llm spans are offered as targets: Phase 0 re-dispatches through the LLM
 * gateway. tool/retriever replay arrives with the Phase 2 SDK harness.
 */

export interface ReplayTargetSpan {
  span_id: string;
  name: string;
  model: string;
  temperature: number | null;
}

interface SpanDelta {
  span_id: string;
  name: string;
  diverged: boolean;
  output_changed: boolean;
  model_changed: boolean;
  cost_delta_usd: number;
  latency_delta_ms: number;
  note: string;
}

interface ReplayResult {
  replay_run_id: string;
  original_run_id: string;
  determinism: string;
  outcome: string;
  span_count_total: number;
  span_count_diverged: number;
  summary: string;
  deltas: SpanDelta[];
}

type EditField = "prompt" | "model" | "temperature";

function outcomeBadgeClass(outcome: string): string {
  if (outcome === "ok") return "badge badge-success";
  if (outcome === "tool_io_missing") return "badge badge-danger";
  return "badge badge-warn"; // model_version_diff, replay_nondeterministic
}

export function ReplayDiffClient({
  runId,
  projectId,
  spans,
}: {
  runId: string;
  projectId: string;
  spans: ReplayTargetSpan[];
}) {
  const [targetId, setTargetId] = useState<string>(spans[0]?.span_id ?? "");
  const [field, setField] = useState<EditField>("prompt");
  const [value, setValue] = useState<string>("");
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (spans.length === 0) {
    return (
      <span style={{ color: "var(--text-3)", fontSize: 12 }}>
        no llm spans to replay (Phase 0 re-dispatches llm spans only)
      </span>
    );
  }

  function run() {
    setError(null);
    const edits =
      value.trim() === ""
        ? []
        : [
            {
              target_span_id: targetId,
              field,
              value:
                field === "temperature" ? Number(value) : value,
            },
          ];
    startTransition(async () => {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/replay`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_id: projectId, edits }),
      });
      if (!res.ok) {
        let detail = `replay failed (${res.status})`;
        try {
          const body = (await res.json()) as { detail?: unknown };
          if (body && body.detail) detail = String(body.detail);
        } catch {
          /* keep default */
        }
        setError(detail);
        return;
      }
      setResult((await res.json()) as ReplayResult);
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <label style={{ display: "grid", gap: 4 }}>
        <FieldLabel>target span</FieldLabel>
        <select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
          {spans.map((s) => (
            <option key={s.span_id} value={s.span_id}>
              {(s.name || "(unnamed)") + " · " + s.span_id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8 }}>
        <label style={{ display: "grid", gap: 4 }}>
          <FieldLabel>edit</FieldLabel>
          <select
            value={field}
            onChange={(e) => setField(e.target.value as EditField)}
          >
            <option value="prompt">prompt</option>
            <option value="model">model</option>
            <option value="temperature">temperature</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 4 }}>
          <FieldLabel>new value (blank = re-run unchanged)</FieldLabel>
          {field === "prompt" ? (
            <textarea
              className="mono"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              rows={3}
              placeholder="edited prompt…"
              style={{ fontSize: 12 }}
            />
          ) : (
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={field === "model" ? "anthropic/claude-opus-4-8" : "0.0"}
            />
          )}
        </label>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={pending}
          onClick={run}
        >
          {pending ? "replaying…" : "Run replay"}
        </button>
      </div>

      {error ? (
        <p
          className="mono"
          style={{ color: "var(--danger)", margin: 0, fontSize: 12 }}
        >
          {error}
        </p>
      ) : null}

      {result ? <ReplayResultView result={result} /> : null}
    </div>
  );
}

function ReplayResultView({ result }: { result: ReplayResult }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span className={outcomeBadgeClass(result.outcome)}>{result.outcome}</span>
        <span className="badge badge-neutral">{result.determinism}</span>
        <span className="mono num" style={{ fontSize: 12, color: "var(--text-3)" }}>
          {result.span_count_diverged}/{result.span_count_total} diverged
        </span>
      </div>
      <p
        className="mono"
        style={{ margin: 0, fontSize: 12, color: "var(--text-2)" }}
      >
        {result.summary}
      </p>
      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: "var(--r-2)",
          overflow: "hidden",
          background: "var(--surface)",
        }}
      >
        {result.deltas.map((d) => (
          <div
            key={d.span_id}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto",
              gap: 8,
              alignItems: "center",
              padding: "6px 10px",
              borderBottom: "1px solid var(--border)",
              fontSize: 11,
              background: d.diverged ? "var(--surface-2)" : "transparent",
            }}
          >
            <span style={{ display: "inline-flex", gap: 8, alignItems: "center", overflow: "hidden" }}>
              <span
                className={`dot ${d.diverged ? "dot-warn" : "dot-success"}`}
                aria-hidden
              />
              <span
                className="mono"
                style={{
                  color: "var(--text-3)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={d.span_id}
              >
                {d.name || d.span_id.slice(0, 8)}
              </span>
              {d.note ? (
                <span style={{ color: "var(--text-3)" }}>· {d.note}</span>
              ) : null}
            </span>
            <span className="mono num" style={{ color: "var(--text-3)" }}>
              {d.output_changed ? "Δout " : ""}
              {d.cost_delta_usd ? `$${d.cost_delta_usd.toFixed(4)} ` : ""}
              {d.latency_delta_ms ? `${d.latency_delta_ms > 0 ? "+" : ""}${d.latency_delta_ms}ms` : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        fontSize: 11,
        color: "var(--text-3)",
        textTransform: "uppercase",
        letterSpacing: 0.4,
      }}
    >
      {children}
    </span>
  );
}
