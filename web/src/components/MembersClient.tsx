"use client";

import { Copy, KeyRound, Plus, Trash2, UserMinus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Workspace membership UI: invite, change role, remove, revoke invitations.
 *
 * Plaintext invitation tokens come back exactly once (Stripe-style). We hold
 * them in component state until the inviter copies them, then they're gone.
 * Per ER-20 revocations are immediate; we just refresh the page after the
 * server returns 204.
 */

export interface Member {
  user_id: string;
  email: string;
  name: string | null;
  role: "admin" | "member" | "viewer";
  created_at: string;
}

export interface Invitation {
  id: string;
  workspace_id: string;
  email: string;
  role: "admin" | "member" | "viewer";
  token_public_id: string;
  invited_by: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

interface InviteResponse {
  invitation: Invitation;
  plaintext_token: string;
}

const ROLES: ("admin" | "member" | "viewer")[] = ["admin", "member", "viewer"];

export function InviteButton({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member" | "viewer">("member");
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<InviteResponse | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setEmail("");
    setRole("member");
    setError(null);
    setRevealed(null);
  }

  function submit() {
    setError(null);
    if (!email.trim()) {
      setError("email is required");
      return;
    }
    startTransition(async () => {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/invitations`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email: email.trim(), role }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `request failed (${res.status})`);
        return;
      }
      const data = (await res.json()) as InviteResponse;
      setRevealed(data);
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
        Invite member
      </button>

      {open && !revealed ? (
        <Backdrop onClose={reset}>
          <div
            style={{
              minWidth: 420,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <div>
              <h2>Invite teammate</h2>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-3)",
                  fontSize: 12,
                }}
              >
                We&apos;ll generate a single-use token. The invitee accepts by
                signing in with that email and posting it to{" "}
                <span className="mono">/v1/invitations/accept</span>.
              </p>
            </div>
            <label
              style={{ display: "flex", flexDirection: "column", gap: 6 }}
            >
              <span style={{ fontSize: 12, color: "var(--text-2)" }}>
                Email
              </span>
              <input
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="alice@acme.com"
                disabled={pending}
              />
            </label>
            <label
              style={{ display: "flex", flexDirection: "column", gap: 6 }}
            >
              <span style={{ fontSize: 12, color: "var(--text-2)" }}>
                Role
              </span>
              <select
                value={role}
                onChange={(e) =>
                  setRole(e.target.value as "admin" | "member" | "viewer")
                }
                disabled={pending}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
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
              style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
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
                {pending ? "Inviting…" : "Send invite"}
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
              minWidth: 480,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <div>
              <h2>Share this token</h2>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-3)",
                  fontSize: 12,
                }}
              >
                You won&apos;t see it again. Send it to{" "}
                <span className="mono">{revealed.invitation.email}</span>.
              </p>
            </div>
            <SecretReveal value={revealed.plaintext_token} />
            <div
              style={{
                display: "flex",
                gap: 8,
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
        gap: 8,
        padding: "10px 12px",
        background: "var(--surface-2)",
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--r-4)",
      }}
    >
      <KeyRound size={14} strokeWidth={1.5} color="var(--text-3)" />
      <code
        className="mono"
        style={{
          flex: 1,
          fontSize: 12,
          color: "var(--text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </code>
      <button
        type="button"
        className="btn btn-sm"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* ignore */
          }
        }}
      >
        <Copy size={12} strokeWidth={1.5} />
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function RoleSelect({
  workspaceId,
  userId,
  current,
}: {
  workspaceId: string;
  userId: string;
  current: "admin" | "member" | "viewer";
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function change(next: string) {
    if (next === current) return;
    setError(null);
    startTransition(async () => {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/members/${userId}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ role: next }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `request failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <select
        value={current}
        onChange={(e) => change(e.target.value)}
        disabled={pending}
      >
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      {error ? (
        <span style={{ fontSize: 11, color: "var(--danger)" }}>{error}</span>
      ) : null}
    </div>
  );
}

export function RemoveMemberButton({
  workspaceId,
  userId,
  email,
}: {
  workspaceId: string;
  userId: string;
  email: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function remove() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/members/${userId}`,
        { method: "DELETE" },
      );
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
        className="btn btn-sm"
        onClick={() => setConfirming(true)}
        title="Remove from workspace"
      >
        <UserMinus size={12} strokeWidth={1.5} />
        Remove
      </button>
      {confirming ? (
        <Backdrop onClose={() => setConfirming(false)}>
          <div
            style={{
              minWidth: 360,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div>
              <h2>Remove member?</h2>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-2)",
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              >
                <span className="mono">{email}</span> will lose access to this
                workspace immediately. They can be re-invited later.
              </p>
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
              style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
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
                onClick={remove}
                disabled={pending}
              >
                {pending ? "Removing…" : "Remove"}
              </button>
            </div>
          </div>
        </Backdrop>
      ) : null}
    </>
  );
}

export function RevokeInviteButton({
  workspaceId,
  invitationId,
  email,
}: {
  workspaceId: string;
  invitationId: string;
  email: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function revoke() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(
        `/api/workspaces/${workspaceId}/invitations/${invitationId}`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `request failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <button
        type="button"
        className="btn btn-sm"
        onClick={revoke}
        disabled={pending}
        title={`Revoke invite for ${email}`}
      >
        <Trash2 size={12} strokeWidth={1.5} />
        {pending ? "Revoking…" : "Revoke"}
      </button>
      {error ? (
        <span style={{ fontSize: 11, color: "var(--danger)" }}>{error}</span>
      ) : null}
    </div>
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
        background: "var(--scrim)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card-elevated"
      >
        {children}
      </div>
    </div>
  );
}
