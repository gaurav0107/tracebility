"use client";

import { ClipboardCheck, Plus, SkipForward, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

/**
 * Client controls for the Annotations queue feature.
 *
 * Three concerns live here:
 *  - NewAnnotationQueueButton: modal form that POSTs /api/annotations
 *    to materialize a queue (sampling rule + rubric snapshot).
 *  - DeleteAnnotationQueueButton: per-row delete, owner/admin only
 *    server-side; we still gate the click behind window.confirm.
 *  - AnnotationLabelForm: the per-item review surface used on the
 *    detail page; submits to /submit or /skip and refreshes server.
 *
 * The queue is materialized at creation, not streamed — re-running
 * the same sampler against ClickHouse on every render is a subtle
 * source of double-counting, and "I have N runs to review" is the
 * mental contract reviewers care about between sessions.
 */

export interface AnnotationSampling {
  window_seconds: number;
  sample_size: number;
  status: "any" | "ok" | "error";
}

export interface AnnotationRubric {
  labels: string[];
  score: "binary" | "scalar" | "none";
}

export interface AnnotationQueueRow {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  sampling: AnnotationSampling;
  rubric: AnnotationRubric;
  item_total: number;
  item_done: number;
  status: "open" | "complete" | "archived";
  created_at: string;
  updated_at: string;
}

export interface AnnotationItemRow {
  id: string;
  queue_id: string;
  project_id: string;
  run_id: string;
  status: "pending" | "done" | "skipped";
  label: string | null;
  score: number | null;
  rationale: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export const SAMPLING_STATUS_OPTIONS: {
  value: AnnotationSampling["status"];
  label: string;
}[] = [
  { value: "any", label: "any — sample all runs" },
  { value: "error", label: "error — only failures" },
  { value: "ok", label: "ok — only successes" },
];

export const SCORE_KIND_OPTIONS: {
  value: AnnotationRubric["score"];
  label: string;
  hint: string;
}[] = [
  {
    value: "binary",
    label: "binary",
    hint: "first label = pass (1.0), every other = fail (0.0)",
  },
  {
    value: "scalar",
    label: "scalar",
    hint: "reviewer types an explicit 0..1 score per item",
  },
  {
    value: "none",
    label: "none",
    hint: "label only — score stored as 0 sentinel",
  },
];

// ---------------------------------------------------------------------------
// New queue
// ---------------------------------------------------------------------------

export function NewAnnotationQueueButton({
  projectId,
}: {
  projectId: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [windowSeconds, setWindowSeconds] = useState("86400");
  const [sampleSize, setSampleSize] = useState("50");
  const [statusFilter, setStatusFilter] =
    useState<AnnotationSampling["status"]>("any");
  const [labelsRaw, setLabelsRaw] = useState("pass, fail");
  const [scoreKind, setScoreKind] =
    useState<AnnotationRubric["score"]>("binary");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setDescription("");
    setWindowSeconds("86400");
    setSampleSize("50");
    setStatusFilter("any");
    setLabelsRaw("pass, fail");
    setScoreKind("binary");
    setError(null);
  }

  function submit() {
    setError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("name required");
      return;
    }
    const windowNum = Number(windowSeconds);
    if (!Number.isFinite(windowNum) || windowNum < 60 || windowNum > 30 * 86400) {
      setError("window must be between 60 and 2592000 seconds (30d)");
      return;
    }
    const sizeNum = Number(sampleSize);
    if (!Number.isFinite(sizeNum) || sizeNum < 1 || sizeNum > 500) {
      setError("sample size must be between 1 and 500");
      return;
    }
    const labels = labelsRaw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    if (labels.length === 0) {
      setError("at least one label required");
      return;
    }
    if (labels.length > 20) {
      setError("max 20 labels");
      return;
    }
    const seen = new Set<string>();
    for (const lbl of labels) {
      if (seen.has(lbl)) {
        setError(`duplicate label: ${lbl}`);
        return;
      }
      seen.add(lbl);
    }
    startTransition(async () => {
      const res = await fetch("/api/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: trimmedName,
          description: description.trim() || null,
          sampling: {
            window_seconds: Math.round(windowNum),
            sample_size: Math.round(sizeNum),
            status: statusFilter,
          },
          rubric: { labels, score: scoreKind },
        }),
      });
      if (!res.ok && res.status !== 201) {
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
      >
        <Plus size={14} /> New queue
      </button>
    );
  }

  const scoreHint =
    SCORE_KIND_OPTIONS.find((s) => s.value === scoreKind)?.hint ?? "";

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
            New annotation queue
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Name" hint="shows up in the list and on the detail page">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. checkout-flow weekly review"
          />
        </Field>
        <Field label="Description (optional)">
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="what this queue is for"
          />
        </Field>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 12,
          }}
        >
          <Field label="Window seconds" hint="60..2592000 (30d)">
            <input
              value={windowSeconds}
              onChange={(e) => setWindowSeconds(e.target.value)}
              inputMode="numeric"
              placeholder="86400"
            />
          </Field>
          <Field label="Sample size" hint="1..500 runs">
            <input
              value={sampleSize}
              onChange={(e) => setSampleSize(e.target.value)}
              inputMode="numeric"
              placeholder="50"
            />
          </Field>
          <Field label="Status filter">
            <select
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(
                  e.target.value as AnnotationSampling["status"],
                )
              }
            >
              {SAMPLING_STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field
          label="Rubric labels"
          hint="comma-separated; first label is the positive class for binary"
        >
          <input
            value={labelsRaw}
            onChange={(e) => setLabelsRaw(e.target.value)}
            placeholder="pass, fail"
          />
        </Field>
        <Field label="Score type" hint={scoreHint}>
          <select
            value={scoreKind}
            onChange={(e) =>
              setScoreKind(e.target.value as AnnotationRubric["score"])
            }
          >
            {SCORE_KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
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
            {pending ? "sampling…" : "Create queue"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delete queue
// ---------------------------------------------------------------------------

export function DeleteAnnotationQueueButton({
  queueId,
  name,
  redirectTo,
}: {
  queueId: string;
  name: string;
  redirectTo?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    const ok = window.confirm(
      `Delete queue "${name}"? Items in the queue are removed too.`,
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/annotations/${queueId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        setError(`delete failed (${res.status})`);
        return;
      }
      if (redirectTo) {
        router.push(redirectTo);
      } else {
        router.refresh();
      }
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12, gap: 4, color: "var(--danger)" }}
      onClick={submit}
      disabled={pending}
      aria-label="delete queue"
      title={error ?? "delete queue"}
    >
      <Trash2 size={14} />
      {pending ? "…" : "delete"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Per-item label form (detail page)
// ---------------------------------------------------------------------------

interface RunPeek {
  name: string;
  status: string;
  inputs: string;
  outputs: string;
  error_message?: string | null;
}

/** Pretty-print boundary I/O for the reviewer; fall back to raw. */
function peekText(raw: string): string {
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw);
    return JSON.stringify(obj, null, 2);
  } catch {
    return raw;
  }
}

export function AnnotationLabelForm({
  queueId,
  item,
  rubric,
  projectId,
}: {
  queueId: string;
  item: AnnotationItemRow;
  rubric: AnnotationRubric;
  projectId: string;
}) {
  const router = useRouter();
  const [peek, setPeek] = useState<RunPeek | null>(null);
  useEffect(() => {
    let alive = true;
    setPeek(null);
    fetch(
      `/api/runs/${encodeURIComponent(item.run_id)}?project_id=${encodeURIComponent(projectId)}`,
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive && d) setPeek(d as RunPeek);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [item.run_id, projectId]);
  const [label, setLabel] = useState<string>(item.label ?? rubric.labels[0] ?? "");
  const [score, setScore] = useState<string>(
    item.score === null ? "" : String(item.score),
  );
  const [rationale, setRationale] = useState<string>(item.rationale ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const isScalar = rubric.score === "scalar";

  function submit() {
    setError(null);
    if (!label) {
      setError("pick a label");
      return;
    }
    let scoreNum: number | null = null;
    if (isScalar) {
      const n = Number(score);
      if (!Number.isFinite(n) || n < 0 || n > 1) {
        setError("scalar score must be between 0 and 1");
        return;
      }
      scoreNum = n;
    }
    startTransition(async () => {
      const res = await fetch(
        `/api/annotations/${queueId}/items/${item.id}/submit`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            label,
            score: scoreNum,
            rationale: rationale.trim() || null,
          }),
        },
      );
      if (!res.ok) {
        let body: unknown;
        try {
          body = await res.json();
        } catch {
          body = null;
        }
        const detail =
          body && typeof body === "object" && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `submit failed (${res.status})`;
        setError(detail);
        return;
      }
      router.refresh();
    });
  }

  function skip() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(
        `/api/annotations/${queueId}/items/${item.id}/skip`,
        { method: "POST" },
      );
      if (!res.ok) {
        setError(`skip failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <div className="card card-pad-lg" style={{ display: "grid", gap: 12 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div>
          <h3 style={{ margin: 0 }}>Label this run</h3>
          <p
            className="mono"
            style={{ fontSize: 11, color: "var(--text-3)", margin: "4px 0 0" }}
          >
            run_id: {item.run_id}
          </p>
        </div>
        <ItemStatusBadge status={item.status} />
      </div>
      {peek ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 10,
          }}
        >
          <div>
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                color: "var(--text-4)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: 6,
              }}
            >
              Input
            </div>
            <pre
              className="mono"
              style={{
                margin: 0,
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--r-3, 8px)",
                padding: "10px 12px",
                fontSize: 12,
                lineHeight: 1.5,
                maxHeight: 180,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                overflowWrap: "break-word",
              }}
            >
              {peekText(peek.inputs) || "(empty)"}
            </pre>
          </div>
          <div>
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                color: "var(--text-4)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: 6,
              }}
            >
              {peek.status === "error" ? "Error" : "Output"}
            </div>
            <pre
              className="mono"
              style={{
                margin: 0,
                background:
                  peek.status === "error"
                    ? "var(--danger-soft)"
                    : "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--r-3, 8px)",
                padding: "10px 12px",
                fontSize: 12,
                lineHeight: 1.5,
                maxHeight: 180,
                overflow: "auto",
                whiteSpace: "pre-wrap",
                overflowWrap: "break-word",
                color:
                  peek.status === "error" ? "var(--danger)" : undefined,
              }}
            >
              {peek.status === "error"
                ? peek.error_message || "(no error message recorded)"
                : peekText(peek.outputs) || "(empty)"}
            </pre>
          </div>
          <a
            href={`/runs/${item.run_id}`}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 12, gridColumn: "1 / -1" }}
          >
            open full trace ↗
          </a>
        </div>
      ) : (
        <div
          style={{
            height: 60,
            background: "var(--surface-3)",
            borderRadius: "var(--r-3, 8px)",
          }}
        />
      )}
      <div>
        <div
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            color: "var(--text-4)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: 6,
          }}
        >
          Label
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {rubric.labels.map((lbl) => {
            const checked = label === lbl;
            return (
              <label
                key={lbl}
                className={checked ? "badge badge-success" : "badge badge-neutral"}
                style={{
                  cursor: "pointer",
                  fontSize: 12,
                  paddingInline: 10,
                  paddingBlock: 6,
                }}
              >
                <input
                  type="radio"
                  name={`label-${item.id}`}
                  value={lbl}
                  checked={checked}
                  onChange={() => setLabel(lbl)}
                  style={{ display: "none" }}
                />
                {lbl}
              </label>
            );
          })}
        </div>
      </div>
      {isScalar ? (
        <Field label="Score" hint="0.0 = worst, 1.0 = best (e.g. 0.85)">
          <input
            value={score}
            onChange={(e) => setScore(e.target.value)}
            inputMode="decimal"
            placeholder="0.85"
          />
        </Field>
      ) : null}
      <Field label="Rationale (optional)">
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          placeholder="why this label? notes for whoever revisits this run"
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
        }}
      >
        <button
          type="button"
          className="btn btn-ghost"
          onClick={skip}
          disabled={pending || item.status !== "pending"}
          title={
            item.status !== "pending"
              ? "only pending items can be skipped"
              : "skip"
          }
        >
          <SkipForward size={14} /> skip
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={submit}
          disabled={pending}
        >
          <ClipboardCheck size={14} />
          {pending
            ? "saving…"
            : item.status === "pending"
              ? "Submit label"
              : "Update label"}
        </button>
      </footer>
    </div>
  );
}

function ItemStatusBadge({ status }: { status: AnnotationItemRow["status"] }) {
  if (status === "done") {
    return <span className="badge badge-success">done</span>;
  }
  if (status === "skipped") {
    return <span className="badge badge-neutral">skipped</span>;
  }
  return <span className="badge badge-warn">pending</span>;
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
    <label style={{ display: "grid", gap: 6 }}>
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
