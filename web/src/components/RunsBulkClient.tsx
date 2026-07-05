"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  useTransition,
} from "react";

/**
 * Bulk-actions on /runs.
 *
 * Three components compose this feature:
 *  - <RunsBulkProvider/> wraps the runs table; tracks selection in
 *    React state (intentionally NOT in URL; the URL is the filter
 *    state, not the selection state).
 *  - <RunCheckbox/> renders inside the per-row table cell; reads/writes
 *    the provider state.
 *  - <BulkActionBar/> renders below the table (sticky bottom); when
 *    selection > 0 it surfaces "Add to dataset" and "Send to
 *    annotation queue" actions. The actions hit the cookie-forwarding
 *    proxy and refresh on success.
 *
 * Selection state is project-scoped: switching projects clears it
 * (we re-mount on a new active project).
 *
 * The bar limits the selection to 200 to match the server cap; the
 * "select all visible" affordance only selects up to 200.
 */

const MAX_SELECTION = 200;

interface BulkContextValue {
  selected: Set<string>;
  toggle: (runId: string) => void;
  selectAll: (runIds: string[]) => void;
  clear: () => void;
}

const BulkContext = createContext<BulkContextValue | null>(null);

export function RunsBulkProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = useCallback((runId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else if (next.size < MAX_SELECTION) {
        next.add(runId);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback((runIds: string[]) => {
    setSelected((prev) => {
      // If everything visible is already selected, clear; otherwise
      // select up to the cap.
      const everyVisibleSelected = runIds.every((id) => prev.has(id));
      if (everyVisibleSelected) {
        const next = new Set(prev);
        for (const id of runIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of runIds) {
        if (next.size >= MAX_SELECTION) break;
        next.add(id);
      }
      return next;
    });
  }, []);

  const clear = useCallback(() => setSelected(new Set()), []);

  const value = useMemo(
    () => ({ selected, toggle, selectAll, clear }),
    [selected, toggle, selectAll, clear],
  );
  return (
    <BulkContext.Provider value={value}>{children}</BulkContext.Provider>
  );
}

function useBulk(): BulkContextValue {
  const ctx = useContext(BulkContext);
  if (!ctx) {
    throw new Error("useBulk must be used inside <RunsBulkProvider>");
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// Per-row checkbox + header "select all visible" checkbox
// ---------------------------------------------------------------------------

// Target checkbox: 16px rounded-6px box, 1.5px border, checked = accent fill
// + white check mark. Shared by the per-row and header checkboxes.
function CheckBox({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-grid",
        placeItems: "center",
        width: 16,
        height: 16,
        borderRadius: 6,
        border: `1.5px solid ${
          checked ? "var(--accent)" : "var(--border-muted)"
        }`,
        background: checked ? "var(--accent)" : "var(--surface)",
        color: "#FFFFFF",
        fontSize: 10,
        fontWeight: 700,
        lineHeight: 1,
      }}
    >
      {checked ? "✓" : ""}
    </span>
  );
}

export function RunCheckbox({ runId }: { runId: string }) {
  const { selected, toggle } = useBulk();
  const checked = selected.has(runId);
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={(e) => {
        e.stopPropagation();
        toggle(runId);
      }}
      style={{
        background: "transparent",
        border: 0,
        cursor: "pointer",
        padding: 0,
        display: "inline-flex",
      }}
    >
      <CheckBox checked={checked} />
    </button>
  );
}

export function SelectAllVisibleCheckbox({
  runIds,
}: {
  runIds: string[];
}) {
  const { selected, selectAll } = useBulk();
  const allChecked =
    runIds.length > 0 && runIds.every((id) => selected.has(id));
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={allChecked}
      onClick={() => selectAll(runIds)}
      title={
        allChecked
          ? "deselect all visible"
          : `select all visible (cap ${MAX_SELECTION})`
      }
      style={{
        background: "transparent",
        border: 0,
        cursor: "pointer",
        padding: 0,
        display: "inline-flex",
      }}
    >
      <CheckBox checked={allChecked} />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Action bar
// ---------------------------------------------------------------------------

export interface DatasetOption {
  id: string;
  slug: string;
  name: string;
}

export interface AnnotationQueueOption {
  id: string;
  name: string;
}

type Mode = "dataset" | "annotation" | null;

export function BulkActionBar({
  projectId,
  datasets,
  queues,
}: {
  projectId: string;
  datasets: DatasetOption[];
  queues: AnnotationQueueOption[];
}) {
  const router = useRouter();
  const { selected, clear } = useBulk();
  const [mode, setMode] = useState<Mode>(null);
  const [datasetId, setDatasetId] = useState<string>(datasets[0]?.id ?? "");
  const [queueId, setQueueId] = useState<string>(queues[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const count = selected.size;
  if (count === 0) return null;

  function submit() {
    setError(null);
    setSummary(null);
    if (mode === "dataset" && !datasetId) {
      setError("pick a dataset");
      return;
    }
    if (mode === "annotation" && !queueId) {
      setError("pick a queue");
      return;
    }
    const runIds = [...selected];
    startTransition(async () => {
      const path =
        mode === "dataset"
          ? "/api/runs/_actions/add-to-dataset"
          : "/api/runs/_actions/add-to-annotation-queue";
      const body: Record<string, unknown> = {
        project_id: projectId,
        run_ids: runIds,
      };
      if (mode === "dataset") {
        body.dataset_id = datasetId;
      } else {
        body.queue_id = queueId;
      }
      const res = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let data: unknown;
        try {
          data = await res.json();
        } catch {
          data = null;
        }
        const detail =
          data && typeof data === "object" && "detail" in data
            ? String((data as { detail: unknown }).detail)
            : `request failed (${res.status})`;
        setError(detail);
        return;
      }
      const data = (await res.json()) as { accepted: number; skipped: number };
      setSummary(
        `accepted ${data.accepted}${
          data.skipped ? ` · skipped ${data.skipped}` : ""
        }`,
      );
      setMode(null);
      // Keep selection so the operator can verify what they did.
      router.refresh();
    });
  }

  return (
    <div
      role="region"
      aria-label="bulk actions"
      style={{
        position: "fixed",
        bottom: 26,
        left: "calc(50% + 118px)",
        transform: "translateX(-50%)",
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        maxWidth: "calc(100vw - 320px)",
        background: "#0C0C10",
        borderRadius: "var(--r-pill)",
        padding: "8px 8px 8px 20px",
        boxShadow: "var(--shadow-bulkbar)",
        zIndex: 40,
      }}
    >
      <span
        className="mono"
        style={{ fontSize: 12, fontWeight: 600, color: "#FFFFFF" }}
      >
        {count} selected
      </span>
      <span
        aria-hidden
        style={{
          width: 1,
          height: 18,
          background: "rgba(255,255,255,0.14)",
          margin: "0 8px",
        }}
      />
      <button
        type="button"
        onClick={() => setMode(mode === "dataset" ? null : "dataset")}
        disabled={datasets.length === 0}
        title={
          datasets.length === 0 ? "create a dataset first" : "add to dataset"
        }
        style={{
          fontSize: 12.5,
          fontWeight: 600,
          color: mode === "dataset" ? "#FFFFFF" : "#D6D6D1",
          background:
            mode === "dataset" ? "rgba(255,255,255,0.08)" : "transparent",
          border: 0,
          borderRadius: "var(--r-pill)",
          padding: "7px 14px",
          cursor: datasets.length === 0 ? "not-allowed" : "pointer",
          opacity: datasets.length === 0 ? 0.4 : 1,
        }}
      >
        add to dataset
      </button>
      <button
        type="button"
        onClick={() => setMode(mode === "annotation" ? null : "annotation")}
        disabled={queues.length === 0}
        title={
          queues.length === 0
            ? "create an annotation queue first"
            : "send to annotation queue"
        }
        style={{
          fontSize: 12.5,
          fontWeight: 600,
          color: mode === "annotation" ? "#FFFFFF" : "#D6D6D1",
          background:
            mode === "annotation" ? "rgba(255,255,255,0.08)" : "transparent",
          border: 0,
          borderRadius: "var(--r-pill)",
          padding: "7px 14px",
          cursor: queues.length === 0 ? "not-allowed" : "pointer",
          opacity: queues.length === 0 ? 0.4 : 1,
        }}
      >
        annotate
      </button>
      {mode === "dataset" ? (
        <select
          value={datasetId}
          onChange={(e) => setDatasetId(e.target.value)}
          className="mono"
          style={{
            minWidth: 200,
            fontSize: 12,
            color: "#FFFFFF",
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: "var(--r-pill)",
            padding: "6px 14px",
          }}
        >
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.slug} — {d.name}
            </option>
          ))}
        </select>
      ) : null}
      {mode === "annotation" ? (
        <select
          value={queueId}
          onChange={(e) => setQueueId(e.target.value)}
          className="mono"
          style={{
            minWidth: 200,
            fontSize: 12,
            color: "#FFFFFF",
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: "var(--r-pill)",
            padding: "6px 14px",
          }}
        >
          {queues.map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
      ) : null}
      {mode != null ? (
        <button
          type="button"
          onClick={submit}
          disabled={pending}
          style={{
            fontSize: 12.5,
            fontWeight: 700,
            color: "#FFFFFF",
            background: "var(--accent)",
            border: 0,
            borderRadius: "var(--r-pill)",
            padding: "7px 18px",
            cursor: pending ? "not-allowed" : "pointer",
            opacity: pending ? 0.7 : 1,
            boxShadow: "0 4px 14px rgba(4,133,247,0.4)",
          }}
        >
          {pending ? "applying…" : "apply"}
        </button>
      ) : null}
      {summary ? (
        <span
          className="mono"
          style={{ fontSize: 11.5, color: "#5FD08A", padding: "0 6px" }}
        >
          {summary}
        </span>
      ) : null}
      {error ? (
        <span
          className="mono"
          style={{ fontSize: 11.5, color: "#F08A7E", padding: "0 6px" }}
        >
          {error}
        </span>
      ) : null}
      <button
        type="button"
        aria-label="clear selection"
        title="clear selection"
        onClick={() => {
          clear();
          setMode(null);
          setSummary(null);
          setError(null);
        }}
        style={{
          fontSize: 13,
          lineHeight: 1,
          color: "#6E6E78",
          background: "transparent",
          border: 0,
          borderRadius: "var(--r-pill)",
          padding: "7px 10px",
          cursor: "pointer",
        }}
      >
        ✕
      </button>
    </div>
  );
}
