"use client";

import { Save, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Workspace SSO config form.
 *
 * V1: one IdP per workspace. The form is "always editable" — there's
 * no separate read view, since SSO config is rare enough that a
 * dedicated edit screen would just be friction. Server enforces
 * owner/admin via cookie session.
 *
 * The client_secret field is write-only; we never echo it back.
 * has_client_secret is shown as a small affordance so operators know
 * the field is set without seeing the value.
 */

export interface SSOConfigRow {
  id: string;
  workspace_id: string;
  issuer: string;
  client_id: string;
  auto_provision: "auto" | "match-only";
  default_role: "owner" | "admin" | "member" | "viewer";
  enabled: boolean;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  jwks_uri: string | null;
  has_client_secret: boolean;
}

const PROVISION_OPTIONS: { value: SSOConfigRow["auto_provision"]; label: string; hint: string }[] = [
  { value: "auto", label: "auto", hint: "create app_user on first sign-in" },
  { value: "match-only", label: "match-only", hint: "require an existing app_user with that email" },
];

const ROLE_OPTIONS: SSOConfigRow["default_role"][] = ["viewer", "member", "admin", "owner"];

export function SSOConfigForm({
  workspaceId,
  initial,
  workspaceSlug,
}: {
  workspaceId: string;
  initial: SSOConfigRow | null;
  workspaceSlug: string;
}) {
  const router = useRouter();
  const [issuer, setIssuer] = useState(initial?.issuer ?? "");
  const [clientId, setClientId] = useState(initial?.client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [autoProvision, setAutoProvision] = useState<SSOConfigRow["auto_provision"]>(
    initial?.auto_provision ?? "auto",
  );
  const [defaultRole, setDefaultRole] = useState<SSOConfigRow["default_role"]>(
    initial?.default_role ?? "member",
  );
  const [enabled, setEnabled] = useState<boolean>(initial?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function save() {
    setError(null);
    setSummary(null);
    if (!issuer.trim() || !clientId.trim()) {
      setError("issuer and client_id are required");
      return;
    }
    // POST replaces (server marks any existing as disabled in a single
    // transaction); PATCH only when we already have an id and the
    // operator is editing an existing row without rotating creds.
    const isPatch = initial != null && !clientSecret;
    if (!isPatch && !clientSecret && !initial?.has_client_secret) {
      setError("client_secret is required for the initial config");
      return;
    }
    startTransition(async () => {
      let res: Response;
      if (isPatch && initial) {
        res = await fetch(`/api/auth/sso/config/${initial.id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            issuer: issuer.trim(),
            client_id: clientId.trim(),
            auto_provision: autoProvision,
            default_role: defaultRole,
            enabled,
          }),
        });
      } else {
        res = await fetch(
          `/api/auth/sso/config?workspace_id=${encodeURIComponent(workspaceId)}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              issuer: issuer.trim(),
              client_id: clientId.trim(),
              client_secret: clientSecret,
              auto_provision: autoProvision,
              default_role: defaultRole,
              enabled,
            }),
          },
        );
      }
      if (!res.ok && res.status !== 200 && res.status !== 201) {
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
      setSummary("saved");
      setClientSecret("");
      router.refresh();
    });
  }

  function disable() {
    if (!initial) return;
    const ok = window.confirm(
      "Disable SSO for this workspace? Existing users keep their accounts; new sign-ins via the IdP will fail until re-enabled.",
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/auth/sso/config/${initial.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: false }),
      });
      if (!res.ok) {
        setError(`disable failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  function remove() {
    if (!initial) return;
    const ok = window.confirm(
      "Delete SSO config? Audit history is kept; the row is removed.",
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/auth/sso/config/${initial.id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        setError(`delete failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  const apiOrigin =
    typeof window !== "undefined" ? window.location.origin : "";

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section className="card card-pad-lg" style={{ display: "grid", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 14.5 }}>OIDC config</h2>
          <p style={{ color: "var(--text-3)", margin: "4px 0 0", fontSize: 12 }}>
            One IdP per workspace. Authorization-code flow with PKCE.
          </p>
        </div>
        <Field
          label="Issuer"
          hint="OIDC discovery URL prefix, e.g. https://<tenant>.auth0.com"
        >
          <input
            value={issuer}
            onChange={(e) => setIssuer(e.target.value)}
            placeholder="https://accounts.example.com"
          />
        </Field>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Client ID">
            <input
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="client-id-from-idp"
            />
          </Field>
          <Field
            label="Client secret"
            hint={
              initial?.has_client_secret
                ? "leave blank to keep existing; type to rotate"
                : "required on first save"
            }
          >
            <input
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="•••••"
              type="password"
            />
          </Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <Field
            label="Auto-provision"
            hint={
              PROVISION_OPTIONS.find((p) => p.value === autoProvision)?.hint
            }
          >
            <select
              value={autoProvision}
              onChange={(e) =>
                setAutoProvision(e.target.value as SSOConfigRow["auto_provision"])
              }
            >
              {PROVISION_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Default role" hint="role assigned on first sign-in">
            <select
              value={defaultRole}
              onChange={(e) =>
                setDefaultRole(e.target.value as SSOConfigRow["default_role"])
              }
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Enabled">
            <select
              value={enabled ? "yes" : "no"}
              onChange={(e) => setEnabled(e.target.value === "yes")}
            >
              <option value="yes">yes</option>
              <option value="no">no (paused)</option>
            </select>
          </Field>
        </div>
        {summary ? (
          <p
            className="mono"
            style={{ color: "var(--success)", margin: 0, fontSize: 12 }}
          >
            {summary}
          </p>
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
            gap: 8,
            justifyContent: "flex-end",
          }}
        >
          {initial ? (
            <>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={disable}
                disabled={pending || !initial.enabled}
                style={{ fontSize: 12 }}
              >
                pause
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={remove}
                disabled={pending}
                style={{ fontSize: 12, color: "var(--danger)" }}
              >
                <Trash2 size={13} /> delete
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="btn btn-primary"
            onClick={save}
            disabled={pending}
            style={{ fontSize: 12 }}
          >
            <Save size={13} />
            {pending ? "saving…" : initial ? "Save changes" : "Save SSO config"}
          </button>
        </footer>
      </section>

      {initial && initial.enabled ? (
        <section className="card card-pad-lg">
          <h2 style={{ marginBottom: 8, fontSize: 14.5 }}>Sign-in URL</h2>
          <p
            style={{
              color: "var(--text-2)",
              margin: "0 0 12px",
              fontSize: 13,
              lineHeight: 1.55,
            }}
          >
            Distribute this URL to users; clicking it kicks off the
            OIDC flow with your IdP and lands them in langprobe on
            success.
          </p>
          <pre
            className="mono"
            style={{
              margin: 0,
              padding: "10px 14px",
              background: "var(--surface-2)",
              border: "1px solid var(--border-soft)",
              borderRadius: "var(--r-4)",
              fontSize: 12,
              wordBreak: "break-all",
              whiteSpace: "pre-wrap",
            }}
          >
            {`${apiOrigin}/api/auth/sso/${workspaceSlug}/start`}
          </pre>
          <p
            className="mono"
            style={{
              color: "var(--text-3)",
              fontSize: 11,
              marginTop: 8,
              lineHeight: 1.55,
            }}
          >
            Discovery cache:{" "}
            {initial.authorization_endpoint
              ? "primed"
              : "fresh on next sign-in"}
            {initial.jwks_uri ? ` · jwks_uri=${initial.jwks_uri}` : ""}
          </p>
        </section>
      ) : null}
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
