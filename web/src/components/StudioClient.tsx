"use client";

import {
  GitBranch,
  PencilLine,
  Play,
  Plus,
  Stamp,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Studio canvas.
 *
 *  - NewBranchButton: modal to draft a branch from a captured run.
 *  - DeleteBranchButton: owner/admin-only; window.confirm in front.
 *  - ReplayBranchButton: v1 stand-in that flips status to 'replayed'
 *    and stamps a synthesized diff_summary. Real LLM runner slots in
 *    later without changing the storage shape (same seam as
 *    comparisons._render_for_variant).
 *  - PromoteBranchButton: replayed -> promoted; wiring into Prompts
 *    revisions lands next iteration.
 *  - StudioEditsEditor: in-place edits editor used on the canvas page;
 *    PATCHes /api/studio/branches/{id} with the new edit list.
 *
 * Edits shape:
 *   {target_span_id: string, field: 'prompt'|'model'|'temperature'|'tool_args',
 *    value: any}
 */

export interface StudioEdit {
  target_span_id: string;
  field: "prompt" | "model" | "temperature" | "tool_args";
  value: unknown;
}

export interface StudioBranchRow {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  source_run_id: string;
  source_span_id: string | null;
  edits: StudioEdit[];
  replay_run_id: string | null;
  status: "draft" | "replayed" | "promoted";
  diff_summary: string | null;
  created_at: string;
  updated_at: string;
  replayed_at: string | null;
}

const EDIT_FIELDS: { value: StudioEdit["field"]; label: string; hint: string }[] = [
  { value: "prompt", label: "prompt", hint: "string — new system / user prompt" },
  { value: "model", label: "model", hint: "string — e.g. claude-sonnet-4-6" },
  { value: "temperature", label: "temperature", hint: "number in [0.0, 2.0]" },
  { value: "tool_args", label: "tool_args", hint: "JSON object — function args override" },
];

// ---------------------------------------------------------------------------
// New branch
// ---------------------------------------------------------------------------

export function NewBranchButton({
  projectId,
  defaultSourceRunId,
}: {
  projectId: string;
  defaultSourceRunId?: string;
}) {
  const router = useRouter();
  // Auto-open the modal when a deep-link arrives with a pre-filled
  // source_run_id (e.g. clicking "branch" from /replay).
  const [open, setOpen] = useState(Boolean(defaultSourceRunId));
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sourceRunId, setSourceRunId] = useState(defaultSourceRunId ?? "");
  const [sourceSpanId, setSourceSpanId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setDescription("");
    setSourceRunId(defaultSourceRunId ?? "");
    setSourceSpanId("");
    setError(null);
  }

  function submit() {
    setError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("name required");
      return;
    }
    const runId = sourceRunId.trim();
    if (!runId) {
      setError("source run id required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/studio/branches", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: trimmedName,
          description: description.trim() || null,
          source_run_id: runId,
          source_span_id: sourceSpanId.trim() || null,
          edits: [],
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
      let branchId: string | null = null;
      try {
        const json = (await res.json()) as { id?: string };
        branchId = json.id ?? null;
      } catch {
        branchId = null;
      }
      reset();
      if (branchId) {
        router.push(`/studio/${branchId}`);
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
      >
        <Plus size={14} /> New branch
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
        style={{ width: "min(560px, 100%)", display: "grid", gap: 12 }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <h2 style={{ margin: 0 }}>New Studio branch</h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Name" hint="shows up on the canvas list and breadcrumb">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. triage-router refuse-on-missing-ctx"
          />
        </Field>
        <Field label="Description (optional)">
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="why this branch exists"
          />
        </Field>
        <Field
          label="Source run id"
          hint="ClickHouse run_id you want to branch from (copy from /runs)"
        >
          <input
            value={sourceRunId}
            onChange={(e) => setSourceRunId(e.target.value)}
            placeholder="01J9Z…QXR"
          />
        </Field>
        <Field
          label="Branch-point span id (optional)"
          hint="leave blank to branch from the run root"
        >
          <input
            value={sourceSpanId}
            onChange={(e) => setSourceSpanId(e.target.value)}
            placeholder="4f2a…"
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
            {pending ? "drafting…" : "Create branch"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delete / Replay / Promote
// ---------------------------------------------------------------------------

export function DeleteBranchButton({
  branchId,
  name,
  redirectTo,
}: {
  branchId: string;
  name: string;
  redirectTo?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    const ok = window.confirm(
      `Delete branch "${name}"? This cannot be undone.`,
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/studio/branches/${branchId}`, {
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
      title={error ?? "delete branch"}
    >
      <Trash2 size={14} />
      {pending ? "…" : "delete"}
    </button>
  );
}

export function ReplayBranchButton({
  branchId,
  disabled,
  label = "Replay",
}: {
  branchId: string;
  disabled?: boolean;
  label?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(`/api/studio/branches/${branchId}/replay`, {
        method: "POST",
      });
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
            : `replay failed (${res.status})`;
        setError(detail);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-primary"
      style={{ fontSize: 12, gap: 6 }}
      onClick={submit}
      disabled={pending || disabled}
      title={error ?? "replay branch"}
    >
      <Play size={13} />
      {pending ? "replaying…" : label}
    </button>
  );
}

export function PromoteBranchButton({
  branchId,
  disabled,
}: {
  branchId: string;
  disabled?: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(`/api/studio/branches/${branchId}/promote`, {
        method: "POST",
      });
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
            : `promote failed (${res.status})`;
        setError(detail);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12, gap: 6 }}
      onClick={submit}
      disabled={pending || disabled}
      title={error ?? "save as candidate prompt"}
    >
      <Stamp size={13} />
      {pending ? "promoting…" : "Promote"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Edits editor (detail page)
// ---------------------------------------------------------------------------

export function StudioEditsEditor({
  branchId,
  initialEdits,
  frozen,
}: {
  branchId: string;
  initialEdits: StudioEdit[];
  frozen: boolean;
}) {
  const router = useRouter();
  const [edits, setEdits] = useState<StudioEdit[]>(initialEdits);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function addEdit() {
    setEdits([
      ...edits,
      {
        target_span_id: "",
        field: "prompt",
        value: "",
      },
    ]);
  }

  function removeEdit(idx: number) {
    setEdits(edits.filter((_, i) => i !== idx));
  }

  function updateEdit(idx: number, patch: Partial<StudioEdit>) {
    setEdits(
      edits.map((e, i) => {
        if (i !== idx) return e;
        const next: StudioEdit = { ...e, ...patch };
        if (patch.field && patch.field !== e.field) {
          if (patch.field === "tool_args") next.value = {};
          else if (patch.field === "temperature") next.value = 0.7;
          else next.value = "";
        }
        return next;
      }),
    );
  }

  function save() {
    setError(null);
    for (let i = 0; i < edits.length; i++) {
      const e = edits[i];
      if (!e.target_span_id.trim()) {
        setError(`edit #${i + 1}: target span id required`);
        return;
      }
      if (e.field === "temperature") {
        const n = Number(e.value);
        if (!Number.isFinite(n) || n < 0 || n > 2) {
          setError(`edit #${i + 1}: temperature must be 0..2`);
          return;
        }
      }
      if (e.field === "tool_args") {
        if (typeof e.value === "string") {
          try {
            JSON.parse(e.value);
          } catch {
            setError(`edit #${i + 1}: tool_args must be valid JSON`);
            return;
          }
        } else if (typeof e.value !== "object" || e.value === null) {
          setError(`edit #${i + 1}: tool_args must be a JSON object`);
          return;
        }
      }
    }
    const cleaned: StudioEdit[] = edits.map((e) => {
      let value: unknown = e.value;
      if (e.field === "temperature") value = Number(e.value);
      if (e.field === "tool_args" && typeof e.value === "string") {
        try {
          value = JSON.parse(e.value);
        } catch {
          value = {};
        }
      }
      return {
        target_span_id: e.target_span_id.trim(),
        field: e.field,
        value,
      };
    });
    startTransition(async () => {
      const res = await fetch(`/api/studio/branches/${branchId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ edits: cleaned }),
      });
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
            : `save failed (${res.status})`;
        setError(detail);
        return;
      }
      router.refresh();
    });
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {frozen ? (
        <p
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)", margin: 0 }}
        >
          edits frozen — this branch has been replayed. Create a new branch
          to keep iterating.
        </p>
      ) : null}
      {edits.length === 0 ? (
        <p
          style={{
            color: "var(--text-2)",
            fontSize: 13,
            margin: 0,
          }}
        >
          No edits yet. Add a target span id and pick a field
          (prompt / model / temperature / tool_args) to start branching.
        </p>
      ) : null}
      {edits.map((edit, idx) => {
        const fieldMeta = EDIT_FIELDS.find((f) => f.value === edit.field);
        return (
          <div
            key={idx}
            className="card"
            style={{
              padding: 12,
              display: "grid",
              gridTemplateColumns: "minmax(160px, 1fr) minmax(140px, 1fr) 2fr auto",
              gap: 8,
              alignItems: "start",
            }}
          >
            <Field label="Target span id">
              <input
                value={edit.target_span_id}
                disabled={frozen}
                onChange={(e) =>
                  updateEdit(idx, { target_span_id: e.target.value })
                }
                placeholder="span_id"
              />
            </Field>
            <Field label="Field" hint={fieldMeta?.hint}>
              <select
                value={edit.field}
                disabled={frozen}
                onChange={(e) =>
                  updateEdit(idx, {
                    field: e.target.value as StudioEdit["field"],
                  })
                }
              >
                {EDIT_FIELDS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Value">
              {edit.field === "tool_args" ? (
                <textarea
                  value={
                    typeof edit.value === "string"
                      ? edit.value
                      : JSON.stringify(edit.value ?? {}, null, 2)
                  }
                  disabled={frozen}
                  onChange={(e) => updateEdit(idx, { value: e.target.value })}
                  rows={4}
                  placeholder={'{"max_tokens": 256}'}
                  style={{ fontFamily: "var(--f-mono)", fontSize: 12 }}
                />
              ) : edit.field === "prompt" ? (
                <textarea
                  value={String(edit.value ?? "")}
                  disabled={frozen}
                  onChange={(e) => updateEdit(idx, { value: e.target.value })}
                  rows={4}
                  placeholder="new prompt text"
                />
              ) : (
                <input
                  value={String(edit.value ?? "")}
                  disabled={frozen}
                  inputMode={edit.field === "temperature" ? "decimal" : "text"}
                  onChange={(e) => updateEdit(idx, { value: e.target.value })}
                  placeholder={
                    edit.field === "model"
                      ? "claude-sonnet-4-6"
                      : edit.field === "temperature"
                        ? "0.7"
                        : ""
                  }
                />
              )}
            </Field>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ alignSelf: "center", color: "var(--danger)" }}
              onClick={() => removeEdit(idx)}
              disabled={frozen}
              aria-label="remove edit"
              title="remove edit"
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={addEdit}
          disabled={frozen}
        >
          <PencilLine size={14} /> Add edit
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={save}
          disabled={frozen || pending}
        >
          <GitBranch size={14} />
          {pending ? "saving…" : "Save edits"}
        </button>
        {error ? (
          <span
            className="mono"
            style={{ color: "var(--danger)", fontSize: 12 }}
          >
            {error}
          </span>
        ) : null}
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
