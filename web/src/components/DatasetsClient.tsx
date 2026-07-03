"use client";

import { Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Datasets list page: create dataset, delete dataset.
 *
 * Create POSTs JSON to /api/datasets and triggers a server-component
 * refresh on success. Slug must match the same regex the server enforces
 * (^[a-z0-9][a-z0-9_-]*$); we surface the server error inline instead of
 * doing client-side validation that could drift.
 */

export interface DatasetRow {
  id: string;
  project_id: string;
  slug: string;
  name: string;
  description: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export function CreateDatasetButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setSlug("");
    setName("");
    setDescription("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!slug.trim() || !name.trim()) {
      setError("slug and name are required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/datasets", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          slug: slug.trim(),
          name: name.trim(),
          description: description.trim() || null,
        }),
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
        <Plus size={14} /> New dataset
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
        style={{ width: "min(520px, 100%)", display: "grid", gap: 12 }}
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
            New dataset
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Slug" hint="lowercase, e.g. regressions-triage">
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="regressions-triage"
            spellCheck={false}
            autoFocus
          />
        </Field>
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Triage regressions"
          />
        </Field>
        <Field label="Description (optional)">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="What this dataset is for…"
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
            {pending ? "creating…" : "Create dataset"}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function DeleteDatasetButton({
  datasetId,
  slug,
}: {
  datasetId: string;
  slug: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function submit() {
    if (!confirm(`Delete dataset "${slug}"? Items remain for audit.`)) return;
    startTransition(async () => {
      const res = await fetch(`/api/datasets/${datasetId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        alert(`Delete failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      onClick={submit}
      disabled={pending}
      aria-label={`Delete dataset ${slug}`}
      title="Delete dataset"
    >
      <Trash2 size={14} />
    </button>
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
      {hint ? (
        <span className="field-hint mono">{hint}</span>
      ) : null}
    </label>
  );
}
