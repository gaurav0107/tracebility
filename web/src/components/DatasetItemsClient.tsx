"use client";

import { Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the dataset detail page: add item, delete item.
 *
 * Items are typed as freeform strings in the API (`input`/`expected`
 * are stored as String in ClickHouse). The form accepts raw text or
 * JSON. We don't validate JSON shape; a malformed item is still a
 * useful regression case.
 */

export function AddItemButton({ datasetId }: { datasetId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [expected, setExpected] = useState("");
  const [metadata, setMetadata] = useState("");
  const [sourceRunId, setSourceRunId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setInput("");
    setExpected("");
    setMetadata("");
    setSourceRunId("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!input.trim()) {
      setError("input is required");
      return;
    }
    let metadataObj: Record<string, unknown> = {};
    if (metadata.trim()) {
      try {
        const parsed = JSON.parse(metadata);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          metadataObj = parsed as Record<string, unknown>;
        } else {
          setError("metadata must be a JSON object");
          return;
        }
      } catch {
        setError("metadata is not valid JSON");
        return;
      }
    }
    startTransition(async () => {
      const res = await fetch(`/api/datasets/${datasetId}/items`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          input,
          expected,
          metadata: metadataObj,
          source_run_id: sourceRunId.trim() || null,
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
        <Plus size={14} /> Add item
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
            Add dataset item
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Input" hint="raw string or JSON; what the agent sees">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={6}
            placeholder='{"messages": [{"role": "user", "content": "..."}]}'
            spellCheck={false}
            autoFocus
          />
        </Field>
        <Field label="Expected output (optional)">
          <textarea
            value={expected}
            onChange={(e) => setExpected(e.target.value)}
            rows={4}
            placeholder="What the agent should have produced"
            spellCheck={false}
          />
        </Field>
        <Field label="Metadata (JSON object, optional)">
          <textarea
            value={metadata}
            onChange={(e) => setMetadata(e.target.value)}
            rows={3}
            placeholder='{"category": "billing", "severity": "high"}'
            spellCheck={false}
          />
        </Field>
        <Field label="Source run id (optional)">
          <input
            value={sourceRunId}
            onChange={(e) => setSourceRunId(e.target.value)}
            placeholder="run_…"
            spellCheck={false}
          />
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
            {pending ? "adding…" : "Add item"}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function DeleteItemButton({
  datasetId,
  itemId,
}: {
  datasetId: string;
  itemId: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function submit() {
    if (!confirm("Delete this item? It remains in storage for audit.")) return;
    startTransition(async () => {
      const res = await fetch(
        `/api/datasets/${datasetId}/items/${itemId}`,
        { method: "DELETE" },
      );
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
      aria-label="Delete item"
      title="Delete item"
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
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}
