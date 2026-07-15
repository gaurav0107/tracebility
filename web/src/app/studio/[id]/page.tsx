import Link from "next/link";
import { notFound } from "next/navigation";
import { Shell } from "@/components/Shell";
import {
  DeleteBranchButton,
  PromoteBranchButton,
  ReplayBranchButton,
  StudioEditsEditor,
  type StudioBranchRow,
} from "@/components/StudioClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";

/**
 * Studio branch detail (canvas).
 *
 * Server component. Loads the branch from postgres and renders:
 *  - breadcrumb + status chip + actions (replay / promote / delete)
 *  - source-run reference (deep link back to /runs/{id})
 *  - in-place edits editor (client component) — frozen post-replay
 *  - diff summary card once the branch has been replayed
 *
 * The canvas is intentionally a flat editor rather than a free-form
 * graph: edits are an ordered list keyed by target_span_id, which is
 * what the replay-runner contract needs. A graph view is a polish layer
 * that lands once we have multiple branches to compare side-by-side.
 */

export const dynamic = "force-dynamic";

export default async function StudioBranchPage({
  params,
}: {
  params: { id: string };
}) {
  const { active, all, reason } = await resolveActiveProject();
  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <div style={{ padding: 24 }}>
          <p style={{ color: "var(--text-2)", fontSize: 13 }}>
            no project resolved{reason ? ` (${reason})` : ""}.{" "}
            <Link href="/studio">back to studio</Link>
          </p>
        </div>
      </Shell>
    );
  }

  const res = await apiGet<StudioBranchRow>(
    `/v1/studio/branches/${params.id}`,
  );
  const branch = res.data;
  if (!branch) {
    notFound();
  }

  // RBAC fail-closed server-side, but if the active project doesn't
  // match the branch we still want to render a clear message — not
  // pretend it's local.
  const crossProject = branch.project_id !== active.id;

  return (
    <Shell active={active} projects={all}>
      <div
        style={{
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 20,
          maxWidth: 1200,
        }}
      >
        <Header
          branch={branch}
          project={active}
          crossProject={crossProject}
        />
        {crossProject ? (
          <div className="card card-pad-lg">
            <p
              style={{
                color: "var(--text-2)",
                margin: 0,
                lineHeight: 1.55,
              }}
            >
              This branch lives in a different project than the active
              one. Switch project in the sidebar to edit it.
            </p>
          </div>
        ) : null}
        <KpiStrip branch={branch} />
        <SourceCard branch={branch} />
        {branch.diff_summary ? (
          <DiffCard summary={branch.diff_summary} replayedAt={branch.replayed_at} />
        ) : null}
        <EditsCard
          branchId={branch.id}
          edits={branch.edits}
          frozen={!(branch.status === "draft" || branch.status === "failed") || crossProject}
        />
      </div>
    </Shell>
  );
}

function Header({
  branch,
  project,
  crossProject,
}: {
  branch: StudioBranchRow;
  project: Project;
  crossProject: boolean;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <div style={{ display: "grid", gap: 4 }}>
        <div
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          <Link href="/studio" style={{ color: "var(--text-3)" }}>
            studio
          </Link>{" "}
          /{" "}
          <span style={{ color: "var(--text-2)" }}>
            {project.slug}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <h1 style={{ margin: 0 }}>{branch.name}</h1>
          <BranchStatusBadge status={branch.status} />
        </div>
        {branch.description ? (
          <p
            style={{
              color: "var(--text-2)",
              margin: 0,
              fontSize: 13,
            }}
          >
            {branch.description}
          </p>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <ReplayBranchButton
          branchId={branch.id}
          disabled={crossProject || branch.status === "promoted"}
          label={
            branch.status === "replayed"
              ? "Re-replay"
              : branch.status === "failed"
                ? "Retry replay"
                : "Replay"
          }
        />
        <PromoteBranchButton
          branchId={branch.id}
          disabled={crossProject || branch.status !== "replayed"}
        />
        <DeleteBranchButton
          branchId={branch.id}
          name={branch.name}
          redirectTo="/studio"
        />
      </div>
    </header>
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

function KpiStrip({ branch }: { branch: StudioBranchRow }) {
  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        gap: 12,
      }}
    >
      <KpiCard label="Edits" value={String(branch.edits.length)} />
      <KpiCard
        label="Branch point"
        value={branch.source_span_id ? "span" : "root"}
      />
      <KpiCard
        label="Created"
        value={fmtDateTime(branch.created_at)}
        mono
      />
      <KpiCard
        label="Replayed"
        value={branch.replayed_at ? fmtDateTime(branch.replayed_at) : "—"}
        mono
      />
    </section>
  );
}

function KpiCard({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
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
      <div
        className={mono ? "mono" : "num"}
        style={{ fontSize: mono ? 14 : 22, color: "var(--text-1)", fontWeight: 500 }}
      >
        {value}
      </div>
    </div>
  );
}

function SourceCard({ branch }: { branch: StudioBranchRow }) {
  return (
    <section className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>Source</h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(100px, max-content) 1fr",
          gap: "8px 16px",
          fontSize: 13,
        }}
      >
        <span style={{ color: "var(--text-3)" }}>run_id</span>
        <Link
          href={`/runs/${branch.source_run_id}`}
          className="mono"
          style={{ color: "var(--link)" }}
        >
          {branch.source_run_id}
        </Link>
        <span style={{ color: "var(--text-3)" }}>branch point</span>
        <span className="mono">
          {branch.source_span_id ? (
            <Link
              href={`/runs/${branch.source_run_id}?span=${encodeURIComponent(branch.source_span_id)}`}
              style={{ color: "var(--link)" }}
            >
              @span {branch.source_span_id}
            </Link>
          ) : (
            <span style={{ color: "var(--text-3)" }}>@root (whole run)</span>
          )}
        </span>
        {branch.replay_run_id ? (
          <>
            <span style={{ color: "var(--text-3)" }}>replay run</span>
            <Link
              href={`/runs/${branch.replay_run_id}`}
              className="mono"
              style={{ color: "var(--link)" }}
            >
              {branch.replay_run_id}
            </Link>
          </>
        ) : null}
      </div>
    </section>
  );
}

function DiffCard({
  summary,
  replayedAt,
}: {
  summary: string;
  replayedAt: string | null;
}) {
  return (
    <section className="card card-pad-lg">
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <h2 style={{ margin: 0 }}>Diff summary</h2>
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          {replayedAt ? `replayed ${fmtDateTime(replayedAt)}` : ""}
        </span>
      </div>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          fontSize: 13,
        }}
      >
        {summary}
      </p>
      <p
        className="mono"
        style={{
          marginTop: 12,
          marginBottom: 0,
          fontSize: 11,
          color: "var(--text-3)",
          lineHeight: 1.55,
        }}
      >
        v1 replay is a stand-in: the diff above is synthesized from the
        edit list. When the LLM runner ships, this card will show a
        per-span output diff against the captured replay artifacts.
      </p>
    </section>
  );
}

function EditsCard({
  branchId,
  edits,
  frozen,
}: {
  branchId: string;
  edits: StudioBranchRow["edits"];
  frozen: boolean;
}) {
  return (
    <section className="card card-pad-lg">
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <h2 style={{ margin: 0 }}>Canvas — edits</h2>
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          {edits.length} edit{edits.length === 1 ? "" : "s"}
        </span>
      </div>
      <StudioEditsEditor
        branchId={branchId}
        initialEdits={edits}
        frozen={frozen}
      />
    </section>
  );
}

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
  } catch {
    return iso;
  }
}
