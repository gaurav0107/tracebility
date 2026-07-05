"use client";

import { Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Evals list page: kick off a new run.
 *
 * Built-in v1 judges only — `echo` (1.0 always, smoke-test), `contains`
 * (input must include expected), `exact` (input must equal expected).
 * The server returns 202 with the queued row; the page server-refreshes
 * to pick it up. The runner background-task fills in scores.
 */

export interface EvalRunRow {
  id: string;
  project_id: string;
  dataset_id: string;
  prompt_id: string | null;
  prompt_version_id: string | null;
  judge_kind: string;
  name: string | null;
  status: string;
  item_total: number;
  item_done: number;
  score_avg: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatasetOption {
  id: string;
  slug: string;
  name: string;
  item_count: number;
}

export function NewEvalRunButton({
  projectId,
  datasets,
}: {
  projectId: string;
  datasets: DatasetOption[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [datasetId, setDatasetId] = useState(datasets[0]?.id ?? "");
  const [judgeKind, setJudgeKind] = useState<"echo" | "contains" | "exact">(
    "echo",
  );
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setDatasetId(datasets[0]?.id ?? "");
    setJudgeKind("echo");
    setName("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!datasetId) {
      setError("pick a dataset first");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/eval-runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          dataset_id: datasetId,
          judge_kind: judgeKind,
          name: name.trim() || null,
        }),
      });
      if (!res.ok && res.status !== 202) {
        let body: unknown;
        try {
          body = await res.json();
        } catch {
          body = null;
        }
        const detail =
          body && typeof body === "object" && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `request failed (${res.status})`;
        setError(detail);
        return;
      }
      reset();
      router.refresh();
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
        disabled={datasets.length === 0}
        title={datasets.length === 0 ? "Create a dataset first" : undefined}
      >
        <Play size={14} /> New eval run
      </button>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--scrim)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "10vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(560px, 100%)", display: "grid", gap: 12 }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 18,
              fontWeight: 800,
              letterSpacing: "-0.02em",
            }}
          >
            New eval run
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Dataset" hint="every item is scored by the judge below">
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
          >
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.slug} — {d.name} ({d.item_count})
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Judge"
          hint="built-in v1; LLM-as-judge swaps in next iteration"
        >
          <select
            value={judgeKind}
            onChange={(e) =>
              setJudgeKind(e.target.value as "echo" | "contains" | "exact")
            }
          >
            <option value="echo">echo — always 1.0 (smoke test)</option>
            <option value="contains">
              contains — pass if expected is substring of input
            </option>
            <option value="exact">exact — pass if input == expected</option>
          </select>
        </Field>
        <Field label="Name (optional)">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. nightly regression on triage-prompt v14"
          />
        </Field>
        {error ? (
          <p
            className="mono"
            style={{ color: "var(--danger)", margin: 0, fontSize: 12 }}
          >
            {error}
          </p>
        ) : null}
        <footer
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 4,
          }}
        >
          <button
            type="button"
            className="btn btn-primary"
            disabled={pending}
            onClick={submit}
          >
            {pending ? "queuing…" : "Run eval"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "grid", gap: 7 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          color: "var(--text-4)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      {children}
      {hint ? (
        <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>{hint}</span>
      ) : null}
    </label>
  );
}
