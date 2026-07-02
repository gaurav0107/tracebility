import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  FilterBar,
  SavedViewsBar,
  type SavedViewRow,
} from "@/components/SavedViewsClient";
import {
  type AnnotationQueueOption,
  BulkActionBar,
  type DatasetOption,
  RunCheckbox,
  RunsBulkProvider,
  SelectAllVisibleCheckbox,
} from "@/components/RunsBulkClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";

/**
 * Traces — full runs list.
 *
 * Server-rendered list of recent runs for the active project. Filters
 * (status / kind / search / window) come from the URL searchParams so
 * they round-trip cleanly with saved views: a SavedViewsBar chip click
 * navigates to /runs?status=error&window=86400, the server re-renders
 * with that filter, and the chip lights up because its filter shape
 * matches the URL.
 *
 * The runs list endpoint accepts the same filter knobs; we forward
 * them straight through.
 */

type Status = "ok" | "error" | "running" | string;

interface Run {
  run_id: string;
  name: string;
  kind: string;
  status: Status;
  start_time: string;
  latency_ms: number | null;
  total_tokens: number;
  cost_usd: number;
  sdk: string;
}

interface RunListResponse {
  items: Run[];
}

const DEFAULT_LIMIT = 200;

const ALLOWED_STATUS = new Set(["ok", "error", "running", "cancelled"]);
const ALLOWED_KIND = new Set([
  "agent",
  "chain",
  "llm",
  "tool",
  "retriever",
  "embedding",
  "parser",
]);

interface AppliedFilters {
  status: string | null;
  kind: string | null;
  search: string | null;
  window_seconds: number | null;
}

function readFilters(
  searchParams: Record<string, string | string[] | undefined>,
): AppliedFilters {
  const pick = (k: string): string | null => {
    const v = searchParams[k];
    if (Array.isArray(v)) return v[0] ?? null;
    return v ?? null;
  };
  const status = pick("status");
  const kind = pick("kind");
  const search = pick("search");
  const window = pick("window");
  let windowSeconds: number | null = null;
  if (window) {
    const n = Number(window);
    if (Number.isFinite(n) && n >= 60 && n <= 30 * 86400) {
      windowSeconds = Math.round(n);
    }
  }
  return {
    status: status && ALLOWED_STATUS.has(status) ? status : null,
    kind: kind && ALLOWED_KIND.has(kind) ? kind : null,
    search: search ? search.slice(0, 256) : null,
    window_seconds: windowSeconds,
  };
}

function buildRunsQuery(projectId: string, filters: AppliedFilters): string {
  const sp = new URLSearchParams({
    project_id: projectId,
    limit: String(DEFAULT_LIMIT),
  });
  if (filters.status) sp.set("status", filters.status);
  if (filters.kind) sp.set("kind", filters.kind);
  if (filters.search) sp.set("search", filters.search);
  if (filters.window_seconds) {
    sp.set("window_seconds", String(filters.window_seconds));
  }
  return `/v1/runs?${sp.toString()}`;
}

export default async function TracesPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const { active, all, reason } = await resolveActiveProject();

  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader title="Traces" subtitle="all runs" />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const filters = readFilters(searchParams);
  const [runsRes, viewsRes, datasetsRes, queuesRes] = await Promise.all([
    apiGet<RunListResponse>(buildRunsQuery(active.id, filters)),
    apiGet<SavedViewRow[]>(
      `/v1/saved-views?project_id=${encodeURIComponent(active.id)}&surface=runs`,
    ),
    apiGet<DatasetListItem[]>(
      `/v1/datasets?project_id=${encodeURIComponent(active.id)}`,
    ),
    apiGet<QueueListItem[]>(
      `/v1/annotations?project_id=${encodeURIComponent(active.id)}`,
    ),
  ]);
  const runs = runsRes.data?.items ?? [];
  const views = viewsRes.data ?? [];
  const datasets: DatasetOption[] = (datasetsRes.data ?? []).map((d) => ({
    id: d.id,
    slug: d.slug,
    name: d.name,
  }));
  const queues: AnnotationQueueOption[] = (queuesRes.data ?? [])
    .filter((q) => q.status !== "archived")
    .map((q) => ({ id: q.id, name: q.name }));
  const visibleRunIds = runs.map((r) => r.run_id);

  const hasFilter =
    filters.status || filters.kind || filters.search || filters.window_seconds;

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <PageHeader
          title="Traces"
          subtitle={`${active.slug} · ${runs.length} ${
            runs.length === 1 ? "run" : "runs"
          }${hasFilter ? " (filtered)" : ` of last ${DEFAULT_LIMIT}`}`}
        />
        <SavedViewsBar projectId={active.id} views={views} />
        <FilterBar projectId={active.id} />
        <RunsBulkProvider>
          <RunsCard
            runs={runs}
            reason={runsRes.error}
            project={active}
            visibleRunIds={visibleRunIds}
          />
          <BulkActionBar
            projectId={active.id}
            datasets={datasets}
            queues={queues}
          />
        </RunsBulkProvider>
      </PageInterior>
    </Shell>
  );
}

interface DatasetListItem {
  id: string;
  slug: string;
  name: string;
}

interface QueueListItem {
  id: string;
  name: string;
  status: string;
}

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 20,
        maxWidth: 1400,
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
    </header>
  );
}

function RunsCard({
  runs,
  reason,
  project,
  visibleRunIds,
}: {
  runs: Run[];
  reason: string | null;
  project: Project;
  visibleRunIds: string[];
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>All runs</h2>
          <span className="card-sub">
            {runs.length} {runs.length === 1 ? "run" : "runs"}
          </span>
        </div>
      </div>
      {runs.length === 0 ? (
        <EmptyRunsState reason={reason} project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 28 }}>
                  <SelectAllVisibleCheckbox runIds={visibleRunIds} />
                </th>
                <th>ID</th>
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
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td style={{ width: 28 }}>
                    <RunCheckbox runId={r.run_id} />
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
      )}
    </section>
  );
}

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", lineHeight: 1.55, margin: 0 }}>
        Run the setup wizard, then point your SDK at this API. See{" "}
        <a href="https://github.com/gaurav0107/langprobe/blob/main/docs/getting-started.md">
          docs/getting-started.md
        </a>
        .
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

function EmptyRunsState({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>
        No runs yet in <span className="mono">{project.slug}</span>.
      </h3>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Send your first trace — see{" "}
        <a href="https://github.com/gaurav0107/langprobe/blob/main/docs/getting-started.md">
          docs/getting-started.md
        </a>
        .
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

function StatusPill({ status }: { status: Status }) {
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
      {status}
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

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}
