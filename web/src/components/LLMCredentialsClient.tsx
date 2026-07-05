"use client";

import { Copy, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Workspace LLM credentials — list, create (reveal-once), revoke.
 *
 * Reveal-once: the server only echoes the plaintext on the POST
 * response. We render a one-shot reveal modal after creation so the
 * operator can copy the secret; navigating away or re-opening the
 * page never shows it again. Same pattern as api_keys.
 *
 * Provider routing: every LLM-dispatching surface (playground,
 * luna prompted-judges, comparisons, studio) reads from this store
 * via `resolve_secret(workspace_id, provider)` with env fallback.
 * So once you save an anthropic key here, the playground stops
 * needing ANTHROPIC_API_KEY in the api service env.
 */

export interface LLMCredentialRow {
  id: string;
  workspace_id: string;
  provider:
    | "anthropic"
    | "openai"
    | "gemini"
    | "mistral"
    | "deepseek"
    | "groq";
  name: string;
  secret_last4: string;
  default_enabled: boolean;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
}

const PROVIDERS: { value: LLMCredentialRow["provider"]; label: string; hint: string }[] = [
  { value: "anthropic", label: "Anthropic", hint: "sk-ant-* keys for Claude" },
  { value: "openai", label: "OpenAI", hint: "sk-proj-* / sk-* keys for GPT and o-series" },
  { value: "gemini", label: "Gemini", hint: "Google AI Studio keys for gemini-*" },
  { value: "mistral", label: "Mistral", hint: "keys.mistral.ai keys for mistral-*" },
  { value: "deepseek", label: "DeepSeek", hint: "platform.deepseek.com keys" },
  { value: "groq", label: "Groq", hint: "console.groq.com keys for hosted llama / mixtral" },
];

export function DefaultEnabledToggle({
  credentialId,
  initial,
}: {
  credentialId: string;
  initial: boolean;
}) {
  const [enabled, setEnabled] = useState(initial);
  const [pending, startTransition] = useTransition();
  return (
    <label
      className="switch"
      style={{ cursor: pending ? "wait" : "pointer" }}
    >
      <input
        type="checkbox"
        checked={enabled}
        disabled={pending}
        onChange={(e) => {
          const next = e.target.checked;
          setEnabled(next);
          startTransition(async () => {
            const res = await fetch(`/api/llm-credentials/${credentialId}`, {
              method: "PATCH",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ default_enabled: next }),
            });
            if (!res.ok) setEnabled(!next);
          });
        }}
      />
      <span className="track" aria-hidden />
      <span style={{ fontSize: 11, color: "var(--text-3)" }}>
        {enabled ? "default for new projects" : "manual link only"}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// New-credential modal
// ---------------------------------------------------------------------------

export function NewLLMCredentialButton({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<LLMCredentialRow["provider"]>("anthropic");
  const [name, setName] = useState("default");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [revealed, setRevealed] = useState<{
    plaintext: string;
    provider: string;
    name: string;
  } | null>(null);

  function reset() {
    setOpen(false);
    setProvider("anthropic");
    setName("default");
    setSecret("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!secret.trim()) {
      setError("paste the provider key first");
      return;
    }
    startTransition(async () => {
      const res = await fetch(
        `/api/llm-credentials?workspace_id=${encodeURIComponent(workspaceId)}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            provider,
            name: name.trim() || "default",
            secret,
          }),
        },
      );
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
      try {
        const json = (await res.json()) as {
          plaintext?: string;
          provider?: string;
          name?: string;
        };
        if (json.plaintext) {
          setRevealed({
            plaintext: json.plaintext,
            provider: json.provider ?? provider,
            name: json.name ?? name,
          });
        }
      } catch {
        // server didn't return the plaintext shape — recover by just
        // closing; the row exists, the operator can revoke + re-create.
      }
      reset();
      router.refresh();
    });
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
      >
        <Plus size={14} /> Add credential
      </button>
      {open ? (
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
            style={{ width: "min(560px, 100%)", display: "grid", gap: 16 }}
          >
            <header
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
              }}
            >
              <h2 style={{ margin: 0 }}>Add LLM credential</h2>
              <button type="button" className="btn btn-ghost" onClick={reset}>
                cancel
              </button>
            </header>

            <Field label="Provider" required>
              <span className="input-shell input-shell-select">
                <select
                  value={provider}
                  onChange={(e) =>
                    setProvider(e.target.value as LLMCredentialRow["provider"])
                  }
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label} — {p.hint}
                    </option>
                  ))}
                </select>
              </span>
            </Field>

            <Field
              label="Name"
              required
              hint="e.g. 'prod', 'staging'; lets you have multiple keys per provider"
            >
              <span className="input-shell">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="default"
                />
              </span>
            </Field>

            <Field
              label="Secret"
              required
              hint="paste the full key — we hash + store, then reveal once on save"
            >
              <span className={`input-shell${error ? " input-shell-error" : ""}`}>
                <input
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder={
                    provider === "anthropic" ? "sk-ant-…" : "sk-proj-…"
                  }
                  type="password"
                />
              </span>
              {error ? (
                <span className="field-error" role="alert">
                  {error}
                </span>
              ) : null}
            </Field>

            <footer
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: 8,
                paddingTop: 14,
                marginTop: 4,
                borderTop: "1px solid var(--border)",
              }}
            >
              <button
                type="button"
                className="btn btn-primary"
                onClick={submit}
                disabled={pending}
              >
                {pending ? "saving…" : "Save credential"}
              </button>
            </footer>
          </div>
        </div>
      ) : null}
      {revealed ? (
        <RevealOnceModal
          revealed={revealed}
          onClose={() => setRevealed(null)}
        />
      ) : null}
    </>
  );
}

function RevealOnceModal({
  revealed,
  onClose,
}: {
  revealed: { plaintext: string; provider: string; name: string };
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
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
        zIndex: 60,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(560px, 100%)", display: "grid", gap: 12 }}
      >
        <header>
          <h2 style={{ margin: 0 }}>Credential saved</h2>
          <p
            style={{
              color: "var(--text-2)",
              margin: "4px 0 0",
              fontSize: 13,
              lineHeight: 1.55,
            }}
          >
            <strong>{revealed.provider}</strong> credential{" "}
            <span className="mono">{revealed.name}</span> is now active. The
            server stores a hash; this is the last time you'll see the
            plaintext. Copy it now.
          </p>
        </header>
        <pre
          className="mono"
          style={{
            margin: 0,
            padding: 12,
            background: "var(--surface-2)",
            borderRadius: "var(--r-4)",
            fontSize: 12,
            wordBreak: "break-all",
            whiteSpace: "pre-wrap",
          }}
        >
          {revealed.plaintext}
        </pre>
        <footer style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(revealed.plaintext);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              } catch {
                // clipboard denied; user can select the pre block manually
              }
            }}
            style={{ fontSize: 12 }}
          >
            <Copy size={13} /> {copied ? "copied" : "copy"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onClose}
            style={{ fontSize: 12 }}
          >
            done
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-row revoke button
// ---------------------------------------------------------------------------

export function RevokeLLMCredentialButton({
  credentialId,
  label,
}: {
  credentialId: string;
  label: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    const ok = window.confirm(
      `Revoke ${label}? Existing in-flight calls keep their key; ` +
        "new dispatches use the next active credential or env fallback.",
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/llm-credentials/${credentialId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        setError(`revoke failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12, gap: 4, color: "var(--danger)" }}
      onClick={submit}
      disabled={pending}
      title={error ?? "revoke credential"}
    >
      <Trash2 size={14} />
      {pending ? "…" : "revoke"}
    </button>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="field-modal">
      <label>
        {label}
        {required ? <span className="req">*</span> : null}
      </label>
      <div className="field-modal-control">
        {children}
        {hint ? <span className="field-hint">{hint}</span> : null}
      </div>
    </div>
  );
}
