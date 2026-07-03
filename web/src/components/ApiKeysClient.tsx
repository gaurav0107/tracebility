"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Interactive controls for API keys: create, reveal-once, revoke.
 *
 * Plaintext keys are returned by the API exactly once; we hold them in
 * component state and never echo them to logs or to the server. After the
 * user dismisses the reveal modal the secret is gone forever — that's the
 * point. Revoke is irreversible (per ER-20: revocation must take effect on
 * the next ingest call, no cache to invalidate).
 */

export interface ApiKey {
  id: string;
  project_id: string;
  public_id: string;
  name: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  expires_at: string | null;
}

interface CreateResponse {
  key: ApiKey;
  plaintext_key: string;
}

export function CreateKeyButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<CreateResponse | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setError(null);
    setRevealed(null);
  }

  function submit() {
    setError(null);
    if (!name.trim()) {
      setError("name is required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/api-keys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: name.trim(),
          scopes: ["ingest:write"],
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `request failed (${res.status})`);
        return;
      }
      const data = (await res.json()) as CreateResponse;
      setRevealed(data);
      setName("");
    });
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
      >
        <Plus size={14} strokeWidth={1.75} />
        New key
      </button>

      {open && !revealed ? (
        <Backdrop onClose={reset}>
          <div
            style={{
              width: 460,
              maxWidth: "calc(100vw - 48px)",
              padding: 28,
              display: "flex",
              flexDirection: "column",
              gap: 20,
            }}
          >
            <div
              style={{ display: "flex", flexDirection: "column", gap: 5 }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span
                  style={{
                    fontSize: 18,
                    fontWeight: 800,
                    letterSpacing: "-0.02em",
                    flex: 1,
                  }}
                >
                  Create API key
                </span>
                <button
                  type="button"
                  onClick={reset}
                  aria-label="Close"
                  style={{
                    border: 0,
                    background: "transparent",
                    color: "var(--text-4)",
                    fontSize: 13,
                    cursor: "pointer",
                    lineHeight: 1,
                    padding: 0,
                  }}
                >
                  ✕
                </button>
              </div>
              <span
                style={{
                  fontSize: 13,
                  color: "var(--text-3)",
                  lineHeight: 1.5,
                }}
              >
                The plaintext is shown once — copy it into your ingest
                environment immediately.
              </span>
            </div>
            <label
              style={{ display: "flex", flexDirection: "column", gap: 7 }}
            >
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  color: "var(--text-4)",
                }}
              >
                Name
              </span>
              <span
                className="input-shell"
                data-error={error ? "true" : undefined}
              >
                <input
                  type="text"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="prod-ingest"
                  disabled={pending}
                />
              </span>
            </label>
            {error ? (
              <p
                style={{
                  color: "var(--danger)",
                  fontSize: 12,
                  margin: 0,
                }}
              >
                {error}
              </p>
            ) : null}
            <div
              style={{
                display: "flex",
                gap: 10,
                justifyContent: "flex-end",
                marginTop: 2,
              }}
            >
              <button
                type="button"
                className="btn"
                onClick={reset}
                disabled={pending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={submit}
                disabled={pending}
              >
                {pending ? "Creating…" : "Create key"}
              </button>
            </div>
          </div>
        </Backdrop>
      ) : null}

      {revealed ? (
        <Backdrop
          onClose={() => {
            reset();
            router.refresh();
          }}
        >
          <div
            style={{
              width: 520,
              maxWidth: "calc(100vw - 48px)",
              padding: 28,
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            <div
              style={{ display: "flex", flexDirection: "column", gap: 5 }}
            >
              <span
                style={{
                  fontSize: 18,
                  fontWeight: 800,
                  letterSpacing: "-0.02em",
                }}
              >
                Save this key
              </span>
              <span
                style={{
                  fontSize: 13,
                  color: "var(--text-3)",
                  lineHeight: 1.5,
                }}
              >
                You won&apos;t see it again. Set it as{" "}
                <span
                  className="mono"
                  style={{
                    fontSize: 12,
                    background: "var(--surface-3)",
                    borderRadius: 5,
                    padding: "1px 6px",
                  }}
                >
                  LANGPROBE_API_KEY
                </span>{" "}
                in your ingest environment.
              </span>
            </div>
            <SecretReveal value={revealed.plaintext_key} />
            <div
              style={{
                display: "flex",
                gap: 10,
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  reset();
                  router.refresh();
                }}
              >
                Done
              </button>
            </div>
          </div>
        </Backdrop>
      ) : null}
    </>
  );
}

function SecretReveal({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 8px 8px 18px",
        background: "var(--surface-2)",
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--r-pill)",
      }}
    >
      <code
        className="mono"
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 12,
          fontWeight: 500,
          color: "var(--text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </code>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* ignore */
          }
        }}
        style={{
          flexShrink: 0,
          border: 0,
          fontSize: 12,
          fontWeight: 700,
          color: "#fff",
          background: "var(--text)",
          borderRadius: "var(--r-pill)",
          padding: "7px 16px",
          cursor: "pointer",
          fontFamily: "var(--f-sans)",
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function RevokeButton({
  keyId,
  name,
}: {
  keyId: string;
  name: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function revoke() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(`/api/api-keys/${keyId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `request failed (${res.status})`);
        return;
      }
      setConfirming(false);
      router.refresh();
    });
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        title="Revoke this key"
        style={{
          border: 0,
          background: "transparent",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--danger)",
          cursor: "pointer",
          padding: 0,
          fontFamily: "var(--f-sans)",
        }}
      >
        revoke
      </button>
      {confirming ? (
        <Backdrop onClose={() => setConfirming(false)}>
          <div
            style={{
              width: 420,
              maxWidth: "calc(100vw - 48px)",
              padding: 28,
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            <div
              style={{ display: "flex", flexDirection: "column", gap: 5 }}
            >
              <span
                style={{
                  fontSize: 18,
                  fontWeight: 800,
                  letterSpacing: "-0.02em",
                }}
              >
                Revoke key?
              </span>
              <span
                style={{
                  color: "var(--text-3)",
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              >
                Any SDK using <span className="mono">{name}</span> will start
                getting <span className="mono">401</span> on the next call.
                This can&apos;t be undone.
              </span>
            </div>
            {error ? (
              <p
                style={{
                  color: "var(--danger)",
                  fontSize: 12,
                  margin: 0,
                }}
              >
                {error}
              </p>
            ) : null}
            <div
              style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
            >
              <button
                type="button"
                className="btn"
                onClick={() => setConfirming(false)}
                disabled={pending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={revoke}
                disabled={pending}
              >
                {pending ? "Revoking…" : "Revoke"}
              </button>
            </div>
          </div>
        </Backdrop>
      ) : null}
    </>
  );
}

function Backdrop({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(12, 12, 16, 0.38)",
        display: "grid",
        placeItems: "center",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--surface)",
          borderRadius: "var(--r-modal)",
          boxShadow: "var(--shadow-modal)",
        }}
      >
        {children}
      </div>
    </div>
  );
}
