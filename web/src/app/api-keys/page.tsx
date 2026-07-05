import { Shell } from "@/components/Shell";
import {
  type ApiKey,
  CreateKeyButton,
  RevokeButton,
} from "@/components/ApiKeysClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";

/**
 * API keys — list, create, revoke.
 *
 * Server-renders the current key list for the active project; client
 * components handle the create/reveal/revoke flow. Plaintext is shown
 * exactly once and never persisted (Stripe-style). Workspace owner/admin
 * can create; member can list. Revoke takes effect on the next ingest call.
 */

export const dynamic = "force-dynamic";

export default async function ApiKeysPage() {
  const { active, all, reason } = await resolveActiveProject();

  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader title="API keys" subtitle="ingest credentials" />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const keysRes = await apiGet<ApiKey[]>(
    `/v1/api_keys?project_id=${encodeURIComponent(active.id)}`,
  );
  const keys = keysRes.data ?? [];

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <PageHeader
          title="API keys"
          subtitle={`${active.slug} · ${keys.length} ${keys.length === 1 ? "key" : "keys"}`}
          right={<CreateKeyButton projectId={active.id} />}
        />
        <SettingsTabs active="api-keys" />
        <KeysCard keys={keys} reason={keysRes.error} project={active} />
        <SetupCard project={active} />
      </PageInterior>
    </Shell>
  );
}

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "28px 32px 32px",
        display: "flex",
        flexDirection: "column",
        gap: 20,
      }}
    >
      {children}
    </div>
  );
}

const SETTINGS_TABS = [
  { key: "api-keys", label: "api keys", href: "/api-keys" },
  { key: "members", label: "members", href: "/members" },
  { key: "workspace", label: "workspace", href: "/workspace" },
  { key: "credentials", label: "llm credentials", href: "/workspace/credentials" },
  { key: "sso", label: "sso", href: "/workspace/sso" },
] as const;

function SettingsTabs({ active }: { active: string }) {
  return (
    <nav
      style={{ display: "flex", gap: 8, flexShrink: 0 }}
      aria-label="Settings"
    >
      {SETTINGS_TABS.map((t) => {
        const isActive = t.key === active;
        return (
          <a
            key={t.key}
            href={t.href}
            aria-current={isActive ? "page" : undefined}
            style={{
              fontSize: 12.5,
              fontWeight: isActive ? 700 : 600,
              color: isActive ? "var(--link)" : "var(--text-2)",
              background: isActive
                ? "var(--accent-fill)"
                : "var(--surface)",
              border: `1px solid ${
                isActive ? "var(--accent-border)" : "var(--border)"
              }`,
              borderRadius: "var(--r-pill)",
              padding: "7px 16px",
              textDecoration: "none",
              whiteSpace: "nowrap",
            }}
          >
            {t.label}
          </a>
        );
      })}
    </nav>
  );
}

function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
        <h1>{title}</h1>
        {subtitle ? (
          <span
            className="mono"
            style={{ fontSize: 12, color: "var(--text-4)" }}
          >
            {subtitle}
          </span>
        ) : null}
      </div>
      {right}
    </header>
  );
}

function KeysCard({
  keys,
  reason,
  project,
}: {
  keys: ApiKey[];
  reason: string | null;
  project: Project;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <h3 className="card-title">API keys</h3>
          <span className="card-sub">
            {keys.length} {keys.length === 1 ? "key" : "keys"} · plaintext shown
            once
          </span>
        </div>
      </div>
      {keys.length === 0 ? (
        <EmptyKeysState reason={reason} project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Status</th>
                <th>Scopes</th>
                <th style={{ textAlign: "right" }}>Created</th>
                <th style={{ textAlign: "right" }}>Last used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const status = k.revoked_at
                  ? "revoked"
                  : isExpired(k.expires_at)
                    ? "expired"
                    : "active";
                return (
                  <tr key={k.id}>
                    <td style={{ fontWeight: 600 }}>{k.name}</td>
                    <td className="mono" style={{ color: "var(--text-2)" }}>
                      lt_{k.public_id}…
                    </td>
                    <td>
                      <StatusPill status={status} />
                    </td>
                    <td>
                      <span
                        style={{ display: "flex", gap: 5, flexWrap: "wrap" }}
                      >
                        {k.scopes.map((s) => (
                          <span
                            key={s}
                            className="mono"
                            style={{
                              fontSize: 10.5,
                              fontWeight: 600,
                              color: "var(--text-2)",
                              background: "var(--surface-3)",
                              borderRadius: "var(--r-pill)",
                              padding: "3px 10px",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {s}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td
                      className="num"
                      style={{ textAlign: "right", color: "var(--text-4)" }}
                    >
                      {fmtDate(k.created_at)}
                    </td>
                    <td
                      className="num"
                      style={{ textAlign: "right", color: "var(--text-4)" }}
                    >
                      {k.last_used_at ? fmtDate(k.last_used_at) : "—"}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {status === "active" ? (
                        <RevokeButton keyId={k.id} name={k.name} />
                      ) : (
                        <span
                          className="mono"
                          style={{ color: "var(--text-4)", fontSize: 11 }}
                        >
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SetupCard({ project }: { project: Project }) {
  return (
    <section className="card card-pad-lg">
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <h3 className="card-title">Use this key</h3>
        <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>
          Set <span className="mono">LANGPROBE_API_KEY</span> in any process
          that ingests traces. The Python and JS SDKs pick it up automatically;
          the LangSmith shim does too.
        </span>
      </div>
      <div
        style={{
          background: "var(--surface-2)",
          border: "1px solid var(--border-soft)",
          borderRadius: "var(--r-4)",
          padding: "14px 18px",
          marginTop: 14,
          fontFamily: "var(--f-mono)",
          fontSize: 12,
          lineHeight: 1.85,
          overflowX: "auto",
        }}
      >
        <div>
          <span style={{ color: "var(--text-4)" }}>$</span> export
          LANGPROBE_API_KEY=<span style={{ color: "var(--link)" }}>lt_…</span>
        </div>
        <div>
          <span style={{ color: "var(--text-4)" }}>$</span> export
          LANGPROBE_PROJECT=
          <span style={{ color: "var(--link)" }}>{project.slug}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-4)" }}>$</span> export
          LANGPROBE_BASE_URL=
          <span style={{ color: "var(--link)" }}>https://langprobe.local</span>
        </div>
        <div style={{ color: "var(--text-4)" }}>
          # LangSmith-compatible drop-in: pip install langprobe-langsmith-shim
        </div>
      </div>
    </section>
  );
}

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Run the setup wizard or create a project before issuing keys.
      </p>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 12, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </div>
  );
}

function EmptyKeysState({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>
        No keys yet for <span className="mono">{project.slug}</span>.
      </h3>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Click <strong>New key</strong> to issue one. The plaintext is shown
        once — copy it into your ingest environment immediately.
      </p>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 12, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "active"
      ? "badge badge-success"
      : status === "expired"
        ? "badge badge-warn"
        : "badge badge-danger";
  const dot =
    status === "active"
      ? "dot dot-success"
      : status === "expired"
        ? "dot dot-warn"
        : "dot dot-danger";
  return (
    <span className={cls}>
      <span className={dot} aria-hidden />
      {status}
    </span>
  );
}

function isExpired(expiresAt: string | null): boolean {
  if (!expiresAt) return false;
  try {
    return new Date(expiresAt).getTime() < Date.now();
  } catch {
    return false;
  }
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}
