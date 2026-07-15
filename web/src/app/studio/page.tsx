import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  DeleteBranchButton,
  NewBranchButton,
  type StudioBranchRow,
} from "@/components/StudioClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";

/**
 * Studio — visual canvas for branching captured runs.
 *
 * Server component. Lists postgres `studio_branch` rows for the
 * active project. Each branch is a derivative of a captured run:
 * pick a span as the branch point, attach an ordered list of edits
 * (prompt / model / temperature / tool_args), replay, optionally
 * promote to a candidate prompt revision.
 *
 * V1 replay is a stand-in (flips status, stamps a synthesized
 * diff_summary). The real LLM runner slots in next iteration without
 * changing the storage shape — same seam as comparisons.
 */

export const dynamic = "force-dynamic";

interface BranchList {
  items: StudioBranchRow[];
}

export default async function StudioPage({
  searchParams,
}: {
  searchParams?: { source_run_id?: string; span_id?: string };
}) {
  const { active, all, reason } = await resolveActiveProject();
  const defaultSourceRunId = (searchParams?.source_run_id ?? "").trim();
  const defaultSpanId = (searchParams?.span_id ?? "").trim();

  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader title="Studio" subtitle="branches of captured runs" />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const res = await apiGet<BranchList>(
    `/v1/studio/branches?project_id=${encodeURIComponent(active.id)}`,
  );
  const branches = res.data?.items ?? [];

  const draftCount = branches.filter((b) => b.status === "draft").length;
  const replayedCount = branches.filter((b) => b.status === "replayed").length;
  const promotedCount = branches.filter((b) => b.status === "promoted").length;

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <PageHeader
          title="Studio"
          subtitle={`${active.slug} · ${branches.length} ${branches.length === 1 ? "branch" : "branches"}`}
          right={
            <NewBranchButton
              projectId={active.id}
              defaultSourceRunId={defaultSourceRunId || undefined}
              defaultSpanId={defaultSpanId || undefined}
            />
          }
        />
        <KpiStrip
          total={branches.length}
          drafts={draftCount}
          replayed={replayedCount}
          promoted={promotedCount}
        />
        <BranchesCard
          rows={branches}
          reason={res.error}
          project={active}
        />
        <UsageCard />
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
        gap: 20,
        maxWidth: 1200,
      }}
    >
      {children}
    </div>
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
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1>{title}</h1>
        {subtitle ? (
          <span
            className="mono"
            style={{ fontSize: 12, color: "var(--text-3)" }}
          >
            {subtitle}
          </span>
        ) : null}
      </div>
      {right}
    </header>
  );
}

function KpiStrip({
  total,
  drafts,
  replayed,
  promoted,
}: {
  total: number;
  drafts: number;
  replayed: number;
  promoted: number;
}) {
  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        gap: 12,
      }}
    >
      <KpiCard label="Branches" value={String(total)} />
      <KpiCard
        label="Drafts"
        value={String(drafts)}
        tone={drafts > 0 ? "warn" : "neutral"}
      />
      <KpiCard label="Replayed" value={String(replayed)} />
      <KpiCard
        label="Promoted"
        value={String(promoted)}
        tone={promoted > 0 ? "success" : "neutral"}
      />
    </section>
  );
}

function KpiCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn" | "neutral" | "success";
}) {
  const color =
    tone === "warn"
      ? "var(--warn, #a36a14)"
      : tone === "success"
        ? "var(--success, #1f7a3a)"
        : "var(--text-1)";
  return (
    <div className="card card-pad-lg">
      <div
        style={{
          fontSize: 11,
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div className="num" style={{ fontSize: 22, color, fontWeight: 500 }}>
        {value}
      </div>
    </div>
  );
}

function BranchesCard({
  rows,
  reason,
  project,
}: {
  rows: StudioBranchRow[];
  reason: string | null;
  project: Project;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>Branches</h2>
          <span className="card-sub">
            scoped to <span className="mono">{project.slug}</span>
          </span>
        </div>
      </div>
      {rows.length === 0 ? (
        <EmptyBranchesState reason={reason} project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Name</th>
                <th>Source</th>
                <th>Edits</th>
                <th>Diff</th>
                <th style={{ textAlign: "right" }}>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.id}>
                  <td>
                    <BranchStatusBadge status={b.status} />
                  </td>
                  <td>
                    <Link
                      href={`/studio/${b.id}`}
                      style={{ textDecoration: "none" }}
                    >
                      <span style={{ color: "var(--link)" }}>{b.name}</span>
                    </Link>
                    {b.description ? (
                      <div
                        style={{
                          fontSize: 12,
                          color: "var(--text-3)",
                          marginTop: 2,
                        }}
                      >
                        {b.description}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <SourceCell
                      runId={b.source_run_id}
                      spanId={b.source_span_id}
                    />
                  </td>
                  <td className="num" style={{ fontSize: 12 }}>
                    {b.edits.length}
                  </td>
                  <td
                    style={{
                      fontSize: 12,
                      color: "var(--text-2)",
                      maxWidth: 320,
                    }}
                  >
                    {b.diff_summary ?? (
                      <span style={{ color: "var(--text-3)" }}>—</span>
                    )}
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {fmtDateTime(b.created_at)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div
                      style={{
                        display: "inline-flex",
                        gap: 4,
                        justifyContent: "flex-end",
                      }}
                    >
                      <Link
                        href={`/studio/${b.id}`}
                        className="btn btn-ghost"
                        style={{ fontSize: 12 }}
                      >
                        open →
                      </Link>
                      <DeleteBranchButton branchId={b.id} name={b.name} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function BranchStatusBadge({
  status,
}: {
  status: StudioBranchRow["status"];
}) {
  if (status === "promoted") {
    return <span className="badge badge-success">promoted</span>;
  }
  if (status === "replayed") {
    return <span className="badge badge-neutral">replayed</span>;
  }
  if (status === "failed") {
    return <span className="badge badge-danger">failed</span>;
  }
  return <span className="badge badge-warn">draft</span>;
}

function SourceCell({
  runId,
  spanId,
}: {
  runId: string;
  spanId: string | null;
}) {
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <Link
        href={`/runs/${runId}`}
        className="mono"
        style={{ fontSize: 12, color: "var(--link)" }}
      >
        {truncate(runId, 18)}
      </Link>
      <span
        className="mono"
        style={{ fontSize: 11, color: "var(--text-3)" }}
      >
        {spanId ? `@span ${truncate(spanId, 10)}` : "@root"}
      </span>
    </div>
  );
}

function UsageCard() {
  return (
    <section className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>How Studio fits</h2>
      <p
        style={{
          color: "var(--text-2)",
          margin: "0 0 12px",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        A branch is a postgres row that points at a captured run, an
        optional branch-point span, and an ordered list of edits
        (prompt, model, temperature, tool_args). The canvas lets you
        author the edit list; Replay flips the branch to{" "}
        <code>replayed</code> with a synthesized <code>diff_summary</code>{" "}
        (the real LLM runner lands next iteration without changing the
        storage shape). Promote marks the branch as a candidate prompt
        revision — wiring into Prompts versions ships in the same step
        as the runner.
      </p>
      <pre style={{ margin: 0 }}>
        {`# python sdk (planned):
from langprobe import studio

branch = studio.branch_from(
    run_id="01J9Z...QXR",
    at_span="4f2a",  # llm_router
    edits=[
        {"target_span_id": "4f2a", "field": "prompt",
         "value": "Be more conservative — refuse if context is missing."},
        {"target_span_id": "4f2a", "field": "model",
         "value": "claude-sonnet-4-6"},
    ],
)

result = branch.replay()
print(result.diff_summary)
# 2 edits: prompt@4f2a (84 chars); model@4f2a → claude-sonnet-4-6
`}
      </pre>
    </section>
  );
}

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Run the setup wizard or create a project before opening Studio.
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

function EmptyBranchesState({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>
        No Studio branches yet in{" "}
        <span className="mono">{project.slug}</span>.
      </h3>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          maxWidth: 640,
        }}
      >
        Click <strong>New branch</strong>, paste a captured{" "}
        <code>run_id</code> from <Link href="/runs">/runs</Link>, and
        author edits on the canvas. Replay tags the branch with a
        diff summary; promote saves it as a candidate prompt revision.
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

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
  } catch {
    return iso;
  }
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return `${s.slice(0, n)}…`;
}
