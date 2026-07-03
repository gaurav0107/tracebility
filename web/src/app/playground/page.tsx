import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  PlaygroundComposer,
  type Message,
  type PlaygroundSessionOut,
  type PromptOption,
} from "@/components/PlaygroundClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject } from "@/lib/projects";

/**
 * Playground — interactive prompt + model invocations.
 *
 * Server component. Fetches the prompt catalog (with versions for each
 * prompt) and the most recent playground sessions, then hands off to
 * the client composer for the interactive bits. Every run lands as a
 * trace in /runs/{id} so the loop closes from playground back to the
 * observability surfaces.
 *
 * v1 is sync (no streaming) and uses env-derived provider credentials
 * on the api service. Per-workspace encrypted credentials slot in a
 * later iteration without changing the URL surface.
 */

export const dynamic = "force-dynamic";

interface PromptRow {
  id: string;
  slug: string;
  name: string;
  version_count: number;
}

interface PromptVersionRow {
  id: string;
  version: number;
  /** Plan B onwards; absent on very old api versions. */
  template_messages?: Message[];
  /** Legacy field; absent eventually. */
  template?: string;
}

interface PromptVersionList {
  versions: PromptVersionRow[];
}

interface PlaygroundSessionList {
  items: PlaygroundSessionOut[];
}

export default async function PlaygroundPage() {
  const { active, all, reason } = await resolveActiveProject();
  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader
            title="Playground"
            subtitle="prompt + model bench"
          />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const [promptsRes, sessionsRes] = await Promise.all([
    apiGet<PromptRow[]>(
      `/v1/prompts?project_id=${encodeURIComponent(active.id)}`,
    ),
    apiGet<PlaygroundSessionList>(
      `/v1/playground/runs?project_id=${encodeURIComponent(active.id)}&limit=20`,
    ),
  ]);

  const prompts = promptsRes.data ?? [];
  const sessions = sessionsRes.data?.items ?? [];

  const promptOptions = await Promise.all(
    prompts.map(async (p): Promise<PromptOption> => {
      const versions = await apiGet<PromptVersionList>(
        `/v1/prompts/${encodeURIComponent(p.id)}/versions`,
      );
      return {
        id: p.id,
        slug: p.slug,
        name: p.name,
        versions: (versions.data?.versions ?? []).map((v) => ({
          id: v.id,
          version: v.version,
          // Prefer the structured field; fall back to wrapping the legacy
          // template (defensive — Plan B's api always sends both).
          template_messages:
            v.template_messages ??
            (v.template != null
              ? [{ role: "human" as const, content: v.template }]
              : []),
          template: v.template ?? "",
        })),
      };
    }),
  );

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <PageHeader
          title="Playground"
          subtitle={`${active.slug} · ${promptOptions.length} ${promptOptions.length === 1 ? "prompt" : "prompts"} in catalog`}
        />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) 360px",
            gap: 20,
            alignItems: "start",
          }}
        >
          <PlaygroundComposer
            projectId={active.id}
            prompts={promptOptions}
          />
          <RecentSessionsCard sessions={sessions} />
        </div>
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

function PageHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        flexShrink: 0,
      }}
    >
      <h1 style={{ textTransform: "lowercase" }}>{title}</h1>
      {subtitle ? (
        <span
          className="mono"
          style={{ fontSize: 12, color: "var(--text-4)" }}
        >
          {subtitle}
        </span>
      ) : null}
      <span style={{ flex: 1 }} />
    </header>
  );
}

function RecentSessionsCard({
  sessions,
}: {
  sessions: PlaygroundSessionOut[];
}) {
  return (
    <section
      className="card"
      style={{ overflow: "hidden", position: "sticky", top: 28 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "18px 24px 12px",
        }}
      >
        <span style={{ fontSize: 14.5, fontWeight: 800 }}>recent sessions</span>
        <span className="card-sub">this project</span>
      </div>
      {sessions.length === 0 ? (
        <div style={{ padding: "4px 24px 20px" }}>
          <p
            style={{
              color: "var(--text-3)",
              margin: 0,
              fontSize: 13,
              lineHeight: 1.55,
            }}
          >
            Your runs will show up here. Each invocation also writes a
            trace under <Link href="/runs">/runs</Link>.
          </p>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            maxHeight: 600,
            overflow: "auto",
          }}
        >
          {sessions.map((s) => (
            <SessionRow key={s.id} session={s} />
          ))}
        </div>
      )}
    </section>
  );
}

function SessionRow({ session }: { session: PlaygroundSessionOut }) {
  const shortId = `pg_${session.id.slice(0, 4)}`;
  const tokens =
    session.total_tokens != null ? `${session.total_tokens} tok` : "— tok";
  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    height: 46,
    padding: "0 24px",
    borderTop: "1px solid var(--divider-row)",
    textDecoration: "none",
  };
  const inner = (
    <>
      <span
        className="mono"
        style={{ fontSize: 12, fontWeight: 600, color: "var(--link)" }}
      >
        {shortId}
      </span>
      <span
        className="mono"
        style={{
          fontSize: 11.5,
          color: "var(--text-2)",
          flex: 1,
          minWidth: 0,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {session.model}
      </span>
      <span
        className="mono"
        style={{ fontSize: 11.5, color: "var(--text-2)" }}
      >
        {tokens}
      </span>
      <SessionStatusBadge status={session.status} />
      <span
        className="mono"
        style={{ fontSize: 11.5, color: "var(--text-4)" }}
      >
        {fmtRelative(session.created_at)}
      </span>
    </>
  );
  if (session.run_id) {
    return (
      <Link href={`/runs/${session.run_id}`} style={rowStyle}>
        {inner}
      </Link>
    );
  }
  return <div style={rowStyle}>{inner}</div>;
}

function SessionStatusBadge({
  status,
}: {
  status: PlaygroundSessionOut["status"];
}) {
  if (status === "done") {
    return <span className="badge badge-success">done</span>;
  }
  if (status === "failed") {
    return <span className="badge badge-danger">failed</span>;
  }
  return <span className="badge badge-warn">{status}</span>;
}

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Run the setup wizard or create a project before opening the
        playground.
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

function fmtRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const seconds = Math.floor((Date.now() - then) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  } catch {
    return iso;
  }
}
