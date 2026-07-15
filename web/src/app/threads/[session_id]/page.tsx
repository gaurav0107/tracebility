import Link from "next/link";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { resolveActiveProject } from "@/lib/projects";

/**
 * Thread detail — chronological turn-by-turn view of a single session.
 *
 * Each row is one run in the session, in start_time asc order. The
 * run_id link goes to the standard /runs/[id] debugger; this page is
 * the conversation-level summary, not the span tree.
 */

export const dynamic = "force-dynamic";

interface ThreadRun {
  run_id: string;
  name: string;
  kind: string;
  status: string;
  start_time: string;
  end_time: string | null;
  latency_ms: number | null;
  total_tokens: number;
  cost_usd: number;
  inputs: string | null;
  outputs: string | null;
  error_message: string | null;
}

interface ThreadDetail {
  session_id: string;
  project_id: string;
  end_user_id: string | null;
  turn_count: number;
  first_run_at: string;
  last_run_at: string;
  total_cost_usd: number;
  total_tokens: number;
  error_count: number;
  runs: ThreadRun[];
}

export default async function ThreadDetailPage({
  params,
}: {
  params: { session_id: string };
}) {
  const sessionId = decodeURIComponent(params.session_id);
  const { active, all, reason } = await resolveActiveProject();

  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader title="Thread" subtitle={sessionId} />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const detailRes = await apiGet<ThreadDetail>(
    `/v1/threads/${encodeURIComponent(sessionId)}?project_id=${encodeURIComponent(active.id)}`,
  );
  const detail = detailRes.data;

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <BreadcrumbBar />
        <PageHeader
          title="Thread"
          subtitle={
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {sessionId}
            </span>
          }
        />
        {detail ? (
          <>
            {detail.end_user_id ? (
              <div style={{ fontSize: 12, color: "var(--text-3)" }}>
                End user:{" "}
                <span className="mono" style={{ color: "var(--text-2)" }}>
                  {detail.end_user_id}
                </span>
              </div>
            ) : null}
            <SummaryGrid detail={detail} />
            <ConversationCard runs={detail.runs} />
            <RunsCard runs={detail.runs} />
          </>
        ) : (
          <NotFoundState reason={detailRes.error} status={detailRes.status} />
        )}
      </PageInterior>
    </Shell>
  );
}

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 16,
        maxWidth: 1400,
      }}
    >
      {children}
    </div>
  );
}

function BreadcrumbBar() {
  return (
    <div style={{ fontSize: 12, color: "var(--text-3)" }}>
      <Link href="/runs?view=threads" style={{ color: "var(--text-3)" }}>
        ← all threads
      </Link>
    </div>
  );
}

function PageHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle: React.ReactNode;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 12,
      }}
    >
      <h1>{title}</h1>
      {subtitle}
    </header>
  );
}

/**
 * Conversation view — the reason a threads surface exists. Extracts
 * the human-readable user message / assistant reply from each run's
 * boundary I/O (JSON shapes vary by SDK; fall back to the raw string)
 * and renders them as a transcript. The metrics table below stays the
 * quantitative view.
 */
function extractUserText(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (typeof obj === "string") return obj;
    if (typeof obj?.question === "string") return obj.question;
    if (typeof obj?.input === "string") return obj.input;
    if (Array.isArray(obj?.messages)) {
      const lastUser = [...obj.messages]
        .reverse()
        .find(
          (m) =>
            (m?.role === "user" || m?.role === "human") &&
            typeof m?.content === "string",
        );
      if (lastUser) return lastUser.content;
    }
  } catch {
    return raw;
  }
  return raw;
}

function extractAssistantText(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (typeof obj === "string") return obj;
    if (typeof obj?.answer === "string") return obj.answer;
    if (typeof obj?.output === "string") return obj.output;
    if (typeof obj?.content === "string") return obj.content;
  } catch {
    return raw;
  }
  return raw;
}

function ConversationCard({ runs }: { runs: ThreadRun[] }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          Conversation{" "}
          <span
            className="mono"
            style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 400 }}
          >
            boundary I/O per turn; open a run for the full trace
          </span>
        </h2>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 20,
          padding: "16px 20px 20px",
        }}
      >
        {runs.map((r, i) => {
          const user = extractUserText(r.inputs);
          const assistant = extractAssistantText(r.outputs);
          return (
            <div
              key={r.run_id}
              style={{ display: "flex", flexDirection: "column", gap: 8 }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 8,
                  fontSize: 11,
                  color: "var(--text-3)",
                }}
              >
                <span style={{ fontWeight: 600 }}>turn {i + 1}</span>
                <span className="mono">
                  {new Date(r.start_time).toISOString().slice(11, 19)}
                </span>
                <Link href={`/runs/${r.run_id}`} className="mono">
                  {r.run_id.slice(0, 8)} →
                </Link>
              </div>
              {user ? (
                <div
                  style={{
                    alignSelf: "flex-start",
                    maxWidth: "72%",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--r-3, 8px)",
                    padding: "10px 14px",
                    fontSize: 13,
                    lineHeight: 1.5,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "break-word",
                  }}
                >
                  {user}
                </div>
              ) : null}
              {r.status === "error" ? (
                <div
                  style={{
                    alignSelf: "flex-end",
                    maxWidth: "72%",
                    background: "var(--danger-soft)",
                    border: "1px solid var(--danger)",
                    borderRadius: "var(--r-3, 8px)",
                    padding: "10px 14px",
                    fontSize: 13,
                    lineHeight: 1.5,
                  }}
                >
                  <span
                    style={{ color: "var(--danger)", fontWeight: 600 }}
                  >
                    run failed
                  </span>
                  {r.error_message ? (
                    <span
                      className="mono"
                      style={{
                        display: "block",
                        marginTop: 4,
                        fontSize: 12,
                        color: "var(--text-2)",
                        overflowWrap: "break-word",
                      }}
                    >
                      {r.error_message}
                    </span>
                  ) : null}
                </div>
              ) : assistant ? (
                <div
                  style={{
                    alignSelf: "flex-end",
                    maxWidth: "72%",
                    background: "var(--accent-soft)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--r-3, 8px)",
                    padding: "10px 14px",
                    fontSize: 13,
                    lineHeight: 1.5,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "break-word",
                  }}
                >
                  {assistant}
                </div>
              ) : (
                <div
                  style={{
                    alignSelf: "flex-end",
                    fontSize: 12,
                    color: "var(--text-3)",
                  }}
                >
                  no output recorded
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SummaryGrid({ detail }: { detail: ThreadDetail }) {
  const durationMs =
    new Date(detail.last_run_at).getTime() -
    new Date(detail.first_run_at).getTime();
  return (
    <section
      className="card"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        gap: 0,
        overflow: "hidden",
      }}
    >
      <KpiCell
        label="Turns"
        value={detail.turn_count.toLocaleString("en-US")}
      />
      <KpiCell
        label="Errors"
        value={detail.error_count > 0 ? String(detail.error_count) : "0"}
        color={detail.error_count > 0 ? "var(--danger)" : undefined}
      />
      <KpiCell
        label="Total tokens"
        value={
          detail.total_tokens
            ? detail.total_tokens.toLocaleString("en-US")
            : "—"
        }
      />
      <KpiCell label="Total cost" value={fmtCost(detail.total_cost_usd)} />
      <KpiCell label="Duration" value={fmtDuration(durationMs)} />
    </section>
  );
}

function KpiCell({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div
      style={{
        padding: "16px 20px",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
        }}
      >
        {label}
      </span>
      <span
        className="num"
        style={{ fontSize: 22, fontWeight: 500, color: color ?? "var(--text)" }}
      >
        {value}
      </span>
    </div>
  );
}

function RunsCard({ runs }: { runs: ThreadRun[] }) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>Turns</h2>
          <span className="card-sub">chronological</span>
        </div>
      </div>
      <div style={{ overflow: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 40 }}>#</th>
              <th>Run</th>
              <th>Name</th>
              <th>Kind</th>
              <th>Status</th>
              <th style={{ textAlign: "right" }}>Latency</th>
              <th style={{ textAlign: "right" }}>Tokens</th>
              <th style={{ textAlign: "right" }}>Cost</th>
              <th style={{ textAlign: "right" }}>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={r.run_id}>
                <td className="num" style={{ color: "var(--text-3)" }}>
                  {i + 1}
                </td>
                <td>
                  <Link href={`/runs/${r.run_id}`} className="mono">
                    {r.run_id.slice(0, 8)}
                  </Link>
                </td>
                <td>{r.name}</td>
                <td>
                  <KindBadge kind={r.kind} />
                </td>
                <td>
                  <StatusPill status={r.status} />
                </td>
                <td className="num" style={{ textAlign: "right" }}>
                  {fmtLatency(r.latency_ms)}
                </td>
                <td className="num" style={{ textAlign: "right" }}>
                  {r.total_tokens
                    ? r.total_tokens.toLocaleString("en-US")
                    : "—"}
                </td>
                <td className="num" style={{ textAlign: "right" }}>
                  {fmtCost(r.cost_usd)}
                </td>
                <td
                  className="num"
                  style={{ textAlign: "right", color: "var(--text-3)" }}
                >
                  {fmtTime(r.start_time)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function NotFoundState({
  reason,
  status,
}: {
  reason: string | null;
  status: number;
}) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>
        {status === 404 ? "Thread not found" : "Unable to load thread"}
      </h2>
      <p style={{ color: "var(--text-2)", lineHeight: 1.55, margin: 0 }}>
        {status === 404
          ? "No runs in the active project share this session_id."
          : "The data plane returned an error."}
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

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", lineHeight: 1.55, margin: 0 }}>
        Run the setup wizard or create a project before viewing sessions.
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

function KindBadge({ kind }: { kind: string }) {
  const k = (kind || "").toLowerCase();
  const cls =
    k === "llm"
      ? "kind-llm"
      : k === "tool"
        ? "kind-tool"
        : k === "retriever" || k === "retr" || k === "reranker"
          ? "kind-retr"
          : "kind-chain";
  return <span className={`kind-badge ${cls}`}>{kind || "chain"}</span>;
}

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "ok"
      ? "badge badge-success"
      : status === "error"
        ? "badge badge-danger"
        : "badge badge-warn";
  const dot =
    status === "ok"
      ? "dot dot-success"
      : status === "error"
        ? "dot dot-danger"
        : "dot dot-warn";
  return (
    <span className={cls}>
      <span className={dot} aria-hidden />
      {status || "running"}
    </span>
  );
}

function fmtLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtCost(usd: number): string {
  if (!usd) return "—";
  return `$${usd.toFixed(4)}`;
}

function fmtDuration(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m`;
  if (ms < 86_400_000) {
    const h = Math.floor(ms / 3_600_000);
    const m = Math.floor((ms % 3_600_000) / 60_000);
    return `${h}h ${m}m`;
  }
  return `${Math.floor(ms / 86_400_000)}d`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}
