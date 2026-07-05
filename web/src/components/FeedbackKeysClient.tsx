"use client";

import { Copy, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Feedback keys page: create key (modal that
 * reveals `plaintext_key` ONCE) and revoke key (immediate, ER-20).
 *
 * Pattern mirrors `ApiKeysClient`: the plaintext is rendered in a
 * post-create reveal panel inside the same modal, with a Copy button
 * and a "Done" action that closes and refreshes. We never call the
 * GET endpoint to refetch a revealed key — that's the whole point of
 * a write-only credential.
 */

export interface FeedbackKeyRow {
  id: string;
  project_id: string;
  public_id: string;
  name: string | null;
  allowed_origins: string[];
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

interface CreateResponse {
  key: FeedbackKeyRow;
  plaintext_key: string;
}

export function CreateFeedbackKeyButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [originsInput, setOriginsInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<CreateResponse | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setOriginsInput("");
    setError(null);
    setRevealed(null);
  }

  function done() {
    reset();
    router.refresh();
  }

  function submit() {
    setError(null);
    if (!name.trim()) {
      setError("name is required");
      return;
    }
    const allowed_origins = originsInput
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    startTransition(async () => {
      const res = await fetch("/api/feedback-keys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: name.trim(),
          allowed_origins,
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
      const payload = (await res.json()) as CreateResponse;
      setRevealed(payload);
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
      >
        <Plus size={14} /> New feedback key
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
        if (e.target === e.currentTarget && !revealed) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(560px, 100%)", display: "grid", gap: 12 }}
      >
        {revealed ? (
          <RevealPanel revealed={revealed} onDone={done} />
        ) : (
          <CreateForm
            name={name}
            setName={setName}
            originsInput={originsInput}
            setOriginsInput={setOriginsInput}
            error={error}
            pending={pending}
            onCancel={reset}
            onSubmit={submit}
          />
        )}
      </div>
    </div>
  );
}

function CreateForm({
  name,
  setName,
  originsInput,
  setOriginsInput,
  error,
  pending,
  onCancel,
  onSubmit,
}: {
  name: string;
  setName: (v: string) => void;
  originsInput: string;
  setOriginsInput: (v: string) => void;
  error: string | null;
  pending: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <>
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
          New feedback key
        </h2>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          cancel
        </button>
      </header>
      <Field label="Name" hint="A label for this key (e.g. 'web-prod')">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="web-prod"
          spellCheck={false}
          autoFocus
        />
      </Field>
      <Field
        label="Allowed origins (optional)"
        hint="comma- or space-separated; leave blank to allow any origin"
      >
        <textarea
          value={originsInput}
          onChange={(e) => setOriginsInput(e.target.value)}
          rows={2}
          placeholder="https://app.example.com, https://staging.example.com"
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
          onClick={onSubmit}
        >
          {pending ? "creating…" : "Create key"}
        </button>
      </footer>
    </>
  );
}

function RevealPanel({
  revealed,
  onDone,
}: {
  revealed: CreateResponse;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(revealed.plaintext_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard might be blocked; user can still select-and-copy
    }
  }

  return (
    <>
      <header>
        <h2
          style={{
            margin: 0,
            fontSize: 18,
            fontWeight: 800,
            letterSpacing: "-0.02em",
          }}
        >
          Save this key
        </h2>
        <p
          style={{
            margin: "4px 0 0",
            color: "var(--text-2)",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          You will not see it again. Drop it into your browser snippet
          and ship. Possession of this key authorizes anyone to post
          feedback against runs in this project; revoke it instantly
          from this page if leaked.
        </p>
      </header>
      <div
        className="mono"
        style={{
          padding: "12px 14px",
          border: "1px solid var(--border-soft)",
          background: "var(--surface-2)",
          fontSize: 13,
          wordBreak: "break-all",
          borderRadius: "var(--r-4)",
        }}
      >
        {revealed.plaintext_key}
      </div>
      <footer
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 4,
        }}
      >
        <button type="button" className="btn btn-ghost" onClick={copy}>
          <Copy size={14} /> {copied ? "copied" : "Copy"}
        </button>
        <button type="button" className="btn btn-primary" onClick={onDone}>
          Done
        </button>
      </footer>
    </>
  );
}

export function RevokeFeedbackKeyButton({
  keyId,
  name,
}: {
  keyId: string;
  name: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function submit() {
    if (!confirm(`Revoke feedback key "${name}"? This cannot be undone.`))
      return;
    startTransition(async () => {
      const res = await fetch(`/api/feedback-keys/${keyId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        alert(`Revoke failed (${res.status})`);
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
      aria-label={`Revoke feedback key ${name}`}
      title="Revoke key"
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
