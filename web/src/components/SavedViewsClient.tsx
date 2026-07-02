"use client";

import { Bookmark, Pin, PinOff, Save, Trash2, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

/**
 * Saved-views bar above the /runs table.
 *
 * Two responsibilities:
 *  - render existing views as chips; clicking applies the view by
 *    pushing its filter shape into the URL (which is the source of
 *    truth for the table's filter state)
 *  - "Save current filter" affordance: opens a modal that posts a
 *    new saved_view with the current URL-derived filter
 *
 * The chip click navigates with router.push so the server component
 * re-fetches with the new filter. Pin and delete also re-fetch via
 * router.refresh after the mutation lands.
 *
 * The "active" check compares the view's filters to the URL filters
 * field-by-field — that way navigating into a view's URL with no
 * matching saved row still renders correctly (and round-tripping a
 * URL into a chip click keeps the chip lit).
 */

export interface SavedViewFilters {
  status?: string | null;
  kind?: string | null;
  search?: string | null;
  window_seconds?: number | null;
  /** /monitoring uses the label form ("1h" / "6h" / "24h" / "7d"). */
  window?: string | null;
  /** /monitoring breakdown filter. */
  model?: string | null;
}

export type SavedViewSurface = "runs" | "monitoring";

export interface SavedViewRow {
  id: string;
  project_id: string;
  name: string;
  surface: SavedViewSurface;
  filters: SavedViewFilters;
  is_shared: boolean;
  pinned: boolean;
  sort_index: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  is_mine: boolean;
}

const STATUS_OPTIONS = [
  { value: "", label: "any status" },
  { value: "ok", label: "ok" },
  { value: "error", label: "error" },
  { value: "running", label: "running" },
  { value: "cancelled", label: "cancelled" },
];

const KIND_OPTIONS = [
  { value: "", label: "any kind" },
  { value: "agent", label: "agent" },
  { value: "chain", label: "chain" },
  { value: "llm", label: "llm" },
  { value: "tool", label: "tool" },
  { value: "retriever", label: "retriever" },
  { value: "reranker", label: "reranker" },
  { value: "embedding", label: "embedding" },
  { value: "parser", label: "parser" },
  { value: "workflow", label: "workflow" },
  { value: "task", label: "task" },
  { value: "guardrail", label: "guardrail" },
  { value: "evaluator", label: "evaluator" },
];

const WINDOW_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "all time" },
  { value: "3600", label: "last 1h" },
  { value: "21600", label: "last 6h" },
  { value: "86400", label: "last 24h" },
  { value: "604800", label: "last 7d" },
];

export function buildSearchParams(
  filters: SavedViewFilters,
  surface: SavedViewSurface = "runs",
): URLSearchParams {
  const sp = new URLSearchParams();
  if (surface === "monitoring") {
    if (filters.window) sp.set("window", filters.window);
    if (filters.model) sp.set("model", filters.model);
    if (filters.kind) sp.set("kind", filters.kind);
    return sp;
  }
  if (filters.status) sp.set("status", filters.status);
  if (filters.kind) sp.set("kind", filters.kind);
  if (filters.search && filters.search.trim()) {
    sp.set("search", filters.search.trim());
  }
  if (filters.window_seconds && filters.window_seconds > 0) {
    sp.set("window", String(filters.window_seconds));
  }
  return sp;
}

function filtersFromSearchParams(
  sp: URLSearchParams,
  surface: SavedViewSurface,
): SavedViewFilters {
  if (surface === "monitoring") {
    const window = sp.get("window");
    return {
      window: window || null,
      model: sp.get("model") || null,
      kind: sp.get("kind") || null,
    };
  }
  const window = sp.get("window");
  return {
    status: sp.get("status") || null,
    kind: sp.get("kind") || null,
    search: sp.get("search") || null,
    window_seconds: window ? Number(window) : null,
  };
}

function filtersEqual(
  a: SavedViewFilters,
  b: SavedViewFilters,
  surface: SavedViewSurface = "runs",
): boolean {
  if (surface === "monitoring") {
    return (
      (a.window || null) === (b.window || null) &&
      (a.model || null) === (b.model || null) &&
      (a.kind || null) === (b.kind || null)
    );
  }
  return (
    (a.status || null) === (b.status || null) &&
    (a.kind || null) === (b.kind || null) &&
    ((a.search || "").trim() || null) === ((b.search || "").trim() || null) &&
    (a.window_seconds || null) === (b.window_seconds || null)
  );
}

// ---------------------------------------------------------------------------
// Bar
// ---------------------------------------------------------------------------

export function SavedViewsBar({
  projectId,
  views,
  surface = "runs",
  basePath = "/runs",
}: {
  projectId: string;
  views: SavedViewRow[];
  surface?: SavedViewSurface;
  basePath?: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentFilters = useMemo(
    () => filtersFromSearchParams(searchParams, surface),
    [searchParams, surface],
  );
  const hasFilter =
    surface === "monitoring"
      ? Boolean(currentFilters.window || currentFilters.model || currentFilters.kind)
      : Boolean(
          currentFilters.status ||
            currentFilters.kind ||
            currentFilters.search ||
            currentFilters.window_seconds,
        );

  const sorted = [...views].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    if (a.sort_index !== b.sort_index) return a.sort_index - b.sort_index;
    return a.created_at.localeCompare(b.created_at);
  });

  function applyView(view: SavedViewRow) {
    const sp = buildSearchParams(view.filters, surface);
    const qs = sp.toString();
    router.push(qs ? `${basePath}?${qs}` : basePath);
  }

  function clearFilter() {
    router.push(basePath);
  }

  return (
    <section
      className="card"
      style={{
        padding: 12,
        display: "flex",
        gap: 12,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <Bookmark size={12} /> Views
      </span>
      {sorted.length === 0 ? (
        <span style={{ color: "var(--text-3)", fontSize: 12 }}>
          no saved views yet
        </span>
      ) : (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {sorted.map((view) => {
            const active = filtersEqual(view.filters, currentFilters, surface);
            return (
              <ViewChip
                key={view.id}
                view={view}
                active={active}
                onApply={() => applyView(view)}
              />
            );
          })}
        </div>
      )}
      <span style={{ flex: 1 }} />
      {hasFilter ? (
        <button
          type="button"
          className="btn btn-ghost"
          onClick={clearFilter}
          style={{ fontSize: 12 }}
        >
          <X size={13} /> clear
        </button>
      ) : null}
      <SaveCurrentViewButton
        projectId={projectId}
        currentFilters={currentFilters}
        surface={surface}
        disabled={!hasFilter}
      />
    </section>
  );
}

function ViewChip({
  view,
  active,
  onApply,
}: {
  view: SavedViewRow;
  active: boolean;
  onApply: () => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function togglePin(e: React.MouseEvent) {
    e.stopPropagation();
    startTransition(async () => {
      await fetch(`/api/saved-views/${view.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ pinned: !view.pinned }),
      });
      router.refresh();
    });
  }

  function deleteView(e: React.MouseEvent) {
    e.stopPropagation();
    const ok = window.confirm(`Delete view "${view.name}"?`);
    if (!ok) return;
    startTransition(async () => {
      await fetch(`/api/saved-views/${view.id}`, { method: "DELETE" });
      router.refresh();
    });
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onApply}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onApply();
      }}
      className={active ? "badge badge-success" : "badge badge-neutral"}
      style={{
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        paddingInline: 10,
        paddingBlock: 6,
        opacity: pending ? 0.6 : 1,
      }}
      title={
        view.is_shared
          ? `shared view: ${view.name}`
          : `your view: ${view.name}`
      }
    >
      {view.is_shared ? (
        <span
          className="mono"
          style={{ fontSize: 10, opacity: 0.7 }}
        >
          shared
        </span>
      ) : null}
      <span style={{ fontSize: 12, fontWeight: active ? 500 : 400 }}>
        {view.name}
      </span>
      {!view.is_shared && view.is_mine ? (
        <button
          type="button"
          onClick={togglePin}
          aria-label={view.pinned ? "unpin" : "pin"}
          title={view.pinned ? "unpin" : "pin"}
          style={{
            background: "transparent",
            border: 0,
            cursor: "pointer",
            padding: 0,
            color: "inherit",
            display: "inline-flex",
          }}
        >
          {view.pinned ? <Pin size={11} /> : <PinOff size={11} />}
        </button>
      ) : null}
      {view.is_mine || view.is_shared ? (
        <button
          type="button"
          onClick={deleteView}
          aria-label="delete view"
          title="delete view"
          style={{
            background: "transparent",
            border: 0,
            cursor: "pointer",
            padding: 0,
            color: "inherit",
            display: "inline-flex",
            opacity: 0.6,
          }}
        >
          <Trash2 size={11} />
        </button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Save modal
// ---------------------------------------------------------------------------

function SaveCurrentViewButton({
  projectId,
  currentFilters,
  surface,
  disabled,
}: {
  projectId: string;
  currentFilters: SavedViewFilters;
  surface: SavedViewSurface;
  disabled: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setShared(false);
    setPinned(false);
    setError(null);
  }

  function submit() {
    setError(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setError("name required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/saved-views", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: trimmed,
          surface,
          filters: currentFilters,
          is_shared: shared,
          pinned: pinned && !shared,
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
        disabled={disabled}
        title={disabled ? "apply a filter first" : "save current filter as a view"}
        style={{ fontSize: 12 }}
      >
        <Save size={13} /> Save view
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
        background: "rgba(10,10,10,0.40)",
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
        className="card card-pad-lg"
        style={{ width: "min(480px, 100%)", display: "grid", gap: 12 }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <h2 style={{ margin: 0 }}>Save view</h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. recent errors"
          />
        </Field>
        <FilterSummary filters={currentFilters} />
        <label
          style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}
        >
          <input
            type="checkbox"
            checked={shared}
            onChange={(e) => setShared(e.target.checked)}
          />
          Share with the project
          <span
            className="mono"
            style={{ marginLeft: 6, fontSize: 11, color: "var(--text-3)" }}
          >
            (everyone in {projectId.slice(0, 6)}…)
          </span>
        </label>
        {!shared ? (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 13,
            }}
          >
            <input
              type="checkbox"
              checked={pinned}
              onChange={(e) => setPinned(e.target.checked)}
            />
            Pin this view (sticks to the front of the list)
          </label>
        ) : null}
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
            {pending ? "saving…" : "Save view"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function FilterSummary({ filters }: { filters: SavedViewFilters }) {
  const parts: string[] = [];
  if (filters.status) parts.push(`status=${filters.status}`);
  if (filters.kind) parts.push(`kind=${filters.kind}`);
  if (filters.search) parts.push(`search="${filters.search}"`);
  if (filters.window_seconds) {
    parts.push(`window=${filters.window_seconds}s`);
  }
  if (filters.window) parts.push(`window=${filters.window}`);
  if (filters.model) parts.push(`model=${filters.model}`);
  return (
    <pre
      className="mono"
      style={{
        margin: 0,
        background: "var(--surface-2)",
        padding: 10,
        borderRadius: 6,
        fontSize: 12,
        whiteSpace: "pre-wrap",
      }}
    >
      {parts.length === 0 ? "no filter applied" : parts.join(" · ")}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Filter bar (separate component, drives URL state)
// ---------------------------------------------------------------------------

export function FilterBar({ projectId: _projectId }: { projectId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initial = useMemo(
    () => filtersFromSearchParams(searchParams, "runs"),
    [searchParams],
  );
  const [statusV, setStatusV] = useState<string>(initial.status ?? "");
  const [kindV, setKindV] = useState<string>(initial.kind ?? "");
  const [searchV, setSearchV] = useState<string>(initial.search ?? "");
  const [windowV, setWindowV] = useState<string>(
    initial.window_seconds ? String(initial.window_seconds) : "",
  );

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const sp = buildSearchParams({
      status: statusV || null,
      kind: kindV || null,
      search: searchV.trim() || null,
      window_seconds: windowV ? Number(windowV) : null,
    });
    const qs = sp.toString();
    router.push(qs ? `/runs?${qs}` : "/runs");
  }

  return (
    <form
      onSubmit={apply}
      className="card"
      style={{
        padding: 12,
        display: "grid",
        gridTemplateColumns: "1fr 160px 160px 160px auto",
        gap: 8,
        alignItems: "end",
      }}
    >
      <Field label="Search" hint="case-insensitive substring on run name">
        <input
          value={searchV}
          onChange={(e) => setSearchV(e.target.value)}
          placeholder="filter by name"
        />
      </Field>
      <Field label="Status">
        <select
          value={statusV}
          onChange={(e) => setStatusV(e.target.value)}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Kind">
        <select value={kindV} onChange={(e) => setKindV(e.target.value)}>
          {KIND_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Window">
        <select value={windowV} onChange={(e) => setWindowV(e.target.value)}>
          {WINDOW_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>
      <button type="submit" className="btn btn-primary" style={{ fontSize: 12 }}>
        Apply
      </button>
    </form>
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
    <label style={{ display: "grid", gap: 4 }}>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
        }}
      >
        {label}
      </span>
      {children}
      {hint ? (
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          {hint}
        </span>
      ) : null}
    </label>
  );
}
