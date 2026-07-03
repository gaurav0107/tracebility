"use client";

import { GanttChart, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * NewPollRunButton — modal that POSTs /api/poll-runs to queue a PoLL
 * (Panel of LLM Judges) run. Operator picks a dataset + ≥2 judges +
 * aggregation strategy; the server scores every item with every judge
 * and computes consensus + pairwise agreement.
 *
 * Judge multi-select: checkboxes, deduped server-side. Aggregation:
 * mean / majority / min / max. Built-in judges only in v1; LLM-as-judge
 * slots in via the playground dispatcher next iteration.
 */

export interface DatasetOption {
  id: string;
  slug: string;
  name: string;
  item_count: number;
}

export interface PollRunRow {
  id: string;
  project_id: string;
  dataset_id: string;
  name: string | null;
  judges: string[];
  aggregation: "mean" | "majority" | "min" | "max";
  status: "queued" | "running" | "done" | "failed";
  item_total: number;
  item_done: number;
  consensus_avg: number | null;
  agreement: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

const JUDGE_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "echo", label: "echo", hint: "smoke-test, always 1.0" },
  { value: "contains", label: "contains", hint: "expected ⊂ input" },
  { value: "exact", label: "exact", hint: "expected == input" },
];

const AGG_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "mean", label: "mean", hint: "average of judge scores" },
  { value: "majority", label: "majority", hint: ">50% pass → consensus pass" },
  { value: "min", label: "min", hint: "most conservative judge wins" },
  { value: "max", label: "max", hint: "most permissive judge wins" },
];

export function NewPollRunButton({
  projectId,
  datasets,
}: {
  projectId: string;
  datasets: DatasetOption[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState<string>(datasets[0]?.id ?? "");
  const [judges, setJudges] = useState<Set<string>>(new Set(["contains", "exact"]));
  const [aggregation, setAggregation] = useState<string>("mean");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function toggleJudge(j: string) {
    setJudges((prev) => {
      const next = new Set(prev);
      if (next.has(j)) {
        next.delete(j);
      } else {
        next.add(j);
      }
      return next;
    });
  }

  function reset() {
    setOpen(false);
    setName("");
    setDatasetId(datasets[0]?.id ?? "");
    setJudges(new Set(["contains", "exact"]));
    setAggregation("mean");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!datasetId) {
      setError("pick a dataset");
      return;
    }
    if (judges.size < 2) {
      setError("pick at least 2 judges");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/poll-runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          dataset_id: datasetId,
          judges: [...judges],
          aggregation,
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
      let id: string | null = null;
      try {
        const json = (await res.json()) as { id?: string };
        id = json.id ?? null;
      } catch {
        id = null;
      }
      reset();
      if (id) {
        router.push(`/poll-runs/${id}`);
      } else {
        router.refresh();
      }
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
        disabled={datasets.length === 0}
        title={datasets.length === 0 ? "create a dataset first" : undefined}
      >
        <Plus size={14} /> New PoLL run
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
        padding: "8vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(640px, 100%)", display: "grid", gap: 12 }}
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
            New PoLL run
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Name (optional)">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. weekly safety panel"
          />
        </Field>
        <Field label="Dataset">
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
          >
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.slug} — {d.name} ({d.item_count} items)
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Judges"
          hint="pick at least 2; built-in deterministic judges in v1"
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 8,
            }}
          >
            {JUDGE_OPTIONS.map((j) => {
              const checked = judges.has(j.value);
              return (
                <label
                  key={j.value}
                  className={checked ? "badge badge-success" : "badge badge-neutral"}
                  style={{
                    cursor: "pointer",
                    paddingInline: 10,
                    paddingBlock: 8,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    gap: 2,
                  }}
                >
                  <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleJudge(j.value)}
                      style={{ accentColor: "var(--accent)" }}
                    />
                    <span
                      className="mono"
                      style={{ fontWeight: 600 }}
                    >
                      {j.label}
                    </span>
                  </span>
                  <span style={{ fontSize: 10.5, color: "var(--text-4)" }}>
                    {j.hint}
                  </span>
                </label>
              );
            })}
          </div>
        </Field>
        <Field label="Aggregation">
          <select
            value={aggregation}
            onChange={(e) => setAggregation(e.target.value)}
          >
            {AGG_OPTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label} — {a.hint}
              </option>
            ))}
          </select>
        </Field>
        {error ? (
          <p className="field-error" style={{ margin: 0 }}>
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
            <GanttChart size={14} />
            {pending ? "queuing…" : "Run panel"}
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
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}
