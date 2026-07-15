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
 * Tracing — one surface, two granularities (LangSmith-style).
 *
 * A segmented switcher toggles between:
 *   - Traces  (?view=traces, default): the flat runs list. Filters
 *     (status / kind / search / window) come from the URL so they
 *     round-trip with saved views.
 *   - Threads (?view=threads): runs grouped by session_id, one row
 *     per multi-turn session.
 *
 * A right-hand stats rail summarizes whatever the table currently
 * shows (count, error rate, latency p50/p99, tokens, cost) so the
 * "how bad is it" read never requires leaving the list. Stats are
 * computed over the fetched page — the scope is printed in the rail
 * header, never implied.
 *
 * /threads redirects here; /threads/[session_id] stays the drill-down.
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

interface ThreadListItem {
  session_id: string;
  turn_count: number;
  first_run_at: string;
  last_run_at: string;
  total_cost_usd: number;
  total_tokens: number;
  error_count: number;
  latency_p95_ms: number | null;
  last_run_id: string;
  last_status: string;
}

interface ThreadListResponse {
  items: ThreadListItem[];
}

const DEFAULT_LIMIT = 200;

const ALLOWED_STATUS = new Set(["ok", "error", "running", "cancelled"]);
// Must cover everything FilterBar's KIND_OPTIONS offers (SavedViewsClient) —
// a kind the whitelist rejects is silently dropped, and the table + stats
// rail then confidently describe the WRONG (unfiltered) population.
const ALLOWED_KIND = new Set([
  "agent",
  "chain",
  "llm",
  "tool",
  "retriever",
  "reranker",
  "embedding",
  "parser",
  "workflow",
  "task",
  "guardrail",
  "evaluator",
]);

type TracingView = "traces" | "threads";

interface AppliedFilters {
  status: string | null;
  kind: string | null;
  search: string | null;
  window_seconds: number | null;
  page: number;
}

function readView(
  searchParams: Record<string, string | string[] | undefined>,
): TracingView {
  const v = searchParams.view;
  const raw = Array.isArray(v) ? v[0] : v;
  return raw === "threads" ? "threads" : "traces";
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
  const pageRaw = Number(pick("page") ?? "1");
  const page =
    Number.isFinite(pageRaw) && pageRaw >= 1 && pageRaw <= 500
      ? Math.floor(pageRaw)
      : 1;
  return {
    status: status && ALLOWED_STATUS.has(status) ? status : null,
    kind: kind && ALLOWED_KIND.has(kind) ? kind : null,
    search: search ? search.slice(0, 256) : null,
    window_seconds: windowSeconds,
    page,
  };
}

function buildRunsQuery(projectId: string, filters: AppliedFilters): string {
  const sp = new URLSearchParams({
    project_id: projectId,
    // One extra row tells us whether an older page exists without a
    // second count query.
    limit: String(DEFAULT_LIMIT + 1),
    offset: String((filters.page - 1) * DEFAULT_LIMIT),
  });
  if (filters.status) sp.set("status", filters.status);
  if (filters.kind) sp.set("kind", filters.kind);
  if (filters.search) sp.set("search", filters.search);
  if (filters.window_seconds) {
    sp.set("window_seconds", String(filters.window_seconds));
  }
  return `/v1/runs?${sp.toString()}`;
}

export default async function TracingPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const { active, all, reason } = await resolveActiveProject();
  const view = readView(searchParams);
  const filters = readFilters(searchParams);
  const crumbs = (
    <>
      {active ? <span className="mono">{active.slug}</span> : null}
      <span className="sep">/</span>
      <span>tracing</span>
      <span className="sep">/</span>
      <span className="last">{view}</span>
    </>
  );

  if (!active) {
    return (
      <Shell active={null} projects={all} crumbs={crumbs}>
        <PageInterior>
          <PageHeader
            title="Tracing"
            subtitle="traces + threads"
            right={<ViewSwitcher view={view} filters={filters} />}
          />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  if (view === "threads") {
    const threadsRes = await apiGet<ThreadListResponse>(
      `/v1/threads?project_id=${encodeURIComponent(active.id)}&limit=${DEFAULT_LIMIT}`,
    );
    const threads = threadsRes.data?.items ?? [];
    const carriedFilter =
      filters.status || filters.kind || filters.search || filters.window_seconds;
    return (
      <Shell active={active} projects={all} crumbs={crumbs}>
        <PageInterior>
          <PageHeader
            title="Tracing"
            subtitle={`${active.slug} · ${threads.length} ${threads.length === 1 ? "session" : "sessions"}`}
            right={<ViewSwitcher view={view} filters={filters} />}
          />
          {carriedFilter ? (
            <p
              style={{
                margin: 0,
                fontSize: 12.5,
                color: "var(--text-3)",
              }}
            >
              Filters apply to the Traces view only — sessions below are
              unfiltered. Your filter is kept and re-applied when you switch
              back.
            </p>
          ) : null}
          <div className="rail-grid">
            <ThreadsCard
              threads={threads}
              reason={threadsRes.error}
              project={active}
            />
            <ThreadStatsRail threads={threads} fetchError={threadsRes.error} />
          </div>
        </PageInterior>
      </Shell>
    );
  }

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
  const fetchedRuns = runsRes.data?.items ?? [];
  const hasOlderPage = fetchedRuns.length > DEFAULT_LIMIT;
  const runs = fetchedRuns.slice(0, DEFAULT_LIMIT);
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
  // Window narrows scope (say which window); other filters change the
  // population (say "filtered"). Both can apply at once.
  const contentFilter = filters.status || filters.kind || filters.search;
  const scopeLabel = filters.window_seconds
    ? `${fmtWindow(filters.window_seconds)}${contentFilter ? " · filtered" : ""}`
    : contentFilter
      ? "filtered"
      : `last ${DEFAULT_LIMIT}`;

  return (
    <Shell active={active} projects={all} crumbs={crumbs}>
      <PageInterior>
        <PageHeader
          title="Tracing"
          subtitle={`${active.slug} · ${runs.length} ${
            runs.length === 1 ? "trace" : "traces"
          }${hasFilter ? " (filtered)" : ` of last ${DEFAULT_LIMIT}`}`}
          right={<ViewSwitcher view={view} filters={filters} />}
        />
        <SavedViewsBar projectId={active.id} views={views} />
        <FilterBar projectId={active.id} />
        <RunsBulkProvider>
          <div className="rail-grid">
            <RunsCard
              runs={runs}
              reason={runsRes.error}
              project={active}
              visibleRunIds={visibleRunIds}
            />
            <RunStatsRail
              runs={runs}
              scopeLabel={scopeLabel}
              fetchError={runsRes.error}
            />
          </div>
          <BulkActionBar
            projectId={active.id}
            datasets={datasets}
            queues={queues}
          />
        </RunsBulkProvider>
        <Pager filters={filters} hasOlder={hasOlderPage} />
      </PageInterior>
    </Shell>
  );
}

function pagerHref(filters: AppliedFilters, page: number): string {
  const sp = new URLSearchParams();
  if (filters.status) sp.set("status", filters.status);
  if (filters.kind) sp.set("kind", filters.kind);
  if (filters.search) sp.set("search", filters.search);
  if (filters.window_seconds) sp.set("window", String(filters.window_seconds));
  if (page > 1) sp.set("page", String(page));
  const qs = sp.toString();
  return qs ? `/runs?${qs}` : "/runs";
}

function Pager({
  filters,
  hasOlder,
}: {
  filters: AppliedFilters;
  hasOlder: boolean;
}) {
  if (filters.page === 1 && !hasOlder) return null;
  return (
    <nav
      aria-label="Pagination"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: 12,
        fontSize: 13,
      }}
    >
      {filters.page > 1 ? (
        <Link href={pagerHref(filters, filters.page - 1)}>← newer</Link>
      ) : (
        <span style={{ color: "var(--text-4)" }}>← newer</span>
      )}
      <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
        page {filters.page}
      </span>
      {hasOlder ? (
        <Link href={pagerHref(filters, filters.page + 1)}>older →</Link>
      ) : (
        <span style={{ color: "var(--text-4)" }}>older →</span>
      )}
    </nav>
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

// ---------------------------------------------------------------------------
// Chrome
// ---------------------------------------------------------------------------

function ViewSwitcher({
  view,
  filters,
}: {
  view: TracingView;
  filters: AppliedFilters;
}) {
  // Carry the filter state across the toggle so peeking at the other
  // view never resets the URL — the URL is the filter's source of truth.
  const filterParams = new URLSearchParams();
  if (filters.status) filterParams.set("status", filters.status);
  if (filters.kind) filterParams.set("kind", filters.kind);
  if (filters.search) filterParams.set("search", filters.search);
  if (filters.window_seconds) {
    filterParams.set("window", String(filters.window_seconds));
  }
  const threadsParams = new URLSearchParams(filterParams);
  threadsParams.set("view", "threads");
  const tracesQs = filterParams.toString();
  return (
    <nav className="seg" aria-label="Tracing view">
      <Link
        href={`/runs?${threadsParams.toString()}`}
        className={`seg-item${view === "threads" ? " active" : ""}`}
        aria-current={view === "threads" ? "page" : undefined}
      >
        Threads
      </Link>
      <Link
        href={tracesQs ? `/runs?${tracesQs}` : "/runs"}
        className={`seg-item${view === "traces" ? " active" : ""}`}
        aria-current={view === "traces" ? "page" : undefined}
      >
        Traces
      </Link>
    </nav>
  );
}

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "28px 32px 32px",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        maxWidth: 1600,
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
        alignItems: "center",
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

// ---------------------------------------------------------------------------
// Stats rails
// ---------------------------------------------------------------------------

function StatsRailCard({
  scopeLabel,
  rows,
}: {
  scopeLabel: string;
  rows: { label: string; value: string; tone?: "danger" }[];
}) {
  return (
    <aside className="card stat-rail" aria-label="Stats">
      <div className="card-head" style={{ paddingBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2 style={{ fontSize: 14.5 }}>Stats</h2>
          <span className="card-sub">{scopeLabel}</span>
        </div>
      </div>
      <div>
        {rows.map((r) => (
          <div className="stat-rail-row" key={r.label}>
            <span className="stat-rail-label">{r.label}</span>
            <span
              className="stat-rail-value"
              style={r.tone === "danger" ? { color: "var(--danger)" } : undefined}
            >
              {r.value}
            </span>
          </div>
        ))}
      </div>
    </aside>
  );
}

function RunStatsRail({
  runs,
  scopeLabel,
  fetchError,
}: {
  runs: Run[];
  scopeLabel: string;
  fetchError: string | null;
}) {
  // A failed fetch must not render as a healthy-and-empty system.
  if (fetchError && runs.length === 0) {
    return (
      <StatsRailCard
        scopeLabel="unavailable"
        rows={STAT_LABELS.map((label) => ({ label, value: "—" }))}
      />
    );
  }
  const count = runs.length;
  const errors = runs.filter((r) => r.status === "error").length;
  const errorRate = count > 0 ? errors / count : null;
  const latencies = runs
    .map((r) => r.latency_ms)
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b);
  const tokens = runs.map((r) => r.total_tokens).sort((a, b) => a - b);
  const totalTokens = tokens.reduce((a, b) => a + b, 0);
  const totalCost = runs.reduce((a, r) => a + r.cost_usd, 0);

  return (
    <StatsRailCard
      scopeLabel={scopeLabel}
      rows={[
        { label: "Traces", value: count.toLocaleString("en-US") },
        {
          label: "Errors",
          value: errors.toLocaleString("en-US"),
          tone: errors > 0 ? "danger" : undefined,
        },
        {
          label: "Error rate",
          value: errorRate === null ? "—" : `${(errorRate * 100).toFixed(1)}%`,
          tone: errorRate !== null && errorRate > 0.01 ? "danger" : undefined,
        },
        { label: "Latency p50", value: fmtLatency(percentile(latencies, 0.5)) },
        { label: "Latency p99", value: fmtLatency(percentile(latencies, 0.99)) },
        { label: "Total tokens", value: fmtTokens(totalTokens) },
        { label: "Median tokens", value: fmtTokens(percentile(tokens, 0.5)) },
        { label: "Total cost", value: fmtCost(totalCost) },
      ]}
    />
  );
}

const STAT_LABELS = [
  "Traces",
  "Errors",
  "Error rate",
  "Latency p50",
  "Latency p99",
  "Total tokens",
  "Median tokens",
  "Total cost",
];

function ThreadStatsRail({
  threads,
  fetchError,
}: {
  threads: ThreadListItem[];
  fetchError: string | null;
}) {
  if (fetchError && threads.length === 0) {
    return (
      <StatsRailCard
        scopeLabel="unavailable"
        rows={THREAD_STAT_LABELS.map((label) => ({ label, value: "—" }))}
      />
    );
  }
  const count = threads.length;
  const turns = threads.reduce((a, t) => a + t.turn_count, 0);
  const withErrors = threads.filter((t) => t.error_count > 0).length;
  const p95s = threads
    .map((t) => t.latency_p95_ms)
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b);
  const totalTokens = threads.reduce((a, t) => a + t.total_tokens, 0);
  const totalCost = threads.reduce((a, t) => a + t.total_cost_usd, 0);

  return (
    <StatsRailCard
      scopeLabel={`last ${DEFAULT_LIMIT}`}
      rows={[
        { label: "Sessions", value: count.toLocaleString("en-US") },
        { label: "Turns", value: turns.toLocaleString("en-US") },
        {
          label: "With errors",
          value: withErrors.toLocaleString("en-US"),
          tone: withErrors > 0 ? "danger" : undefined,
        },
        {
          label: "Turns / session",
          value: count > 0 ? (turns / count).toFixed(1) : "—",
        },
        { label: "Median p95", value: fmtLatency(percentile(p95s, 0.5)) },
        { label: "Total tokens", value: fmtTokens(totalTokens) },
        { label: "Total cost", value: fmtCost(totalCost) },
      ]}
    />
  );
}

const THREAD_STAT_LABELS = [
  "Sessions",
  "Turns",
  "With errors",
  "Turns / session",
  "Median p95",
  "Total tokens",
  "Total cost",
];

function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const idx = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil(p * sorted.length) - 1),
  );
  return sorted[idx];
}

// ---------------------------------------------------------------------------
// Traces table
// ---------------------------------------------------------------------------

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
          <h2>Traces</h2>
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
                    style={{ textAlign: "right", color: "var(--text-4)" }}
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

// ---------------------------------------------------------------------------
// Threads table
// ---------------------------------------------------------------------------

function ThreadsCard({
  threads,
  reason,
  project,
}: {
  threads: ThreadListItem[];
  reason: string | null;
  project: Project;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>Threads</h2>
          <span className="card-sub">runs grouped by session_id</span>
        </div>
      </div>
      {threads.length === 0 ? (
        <EmptyThreadsState reason={reason} project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Session</th>
                <th style={{ textAlign: "right" }}>Turns</th>
                <th>Last status</th>
                <th style={{ textAlign: "right" }}>Errors</th>
                <th style={{ textAlign: "right" }}>p95 latency</th>
                <th style={{ textAlign: "right" }}>Tokens</th>
                <th style={{ textAlign: "right" }}>Cost</th>
                <th style={{ textAlign: "right" }}>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {threads.map((t) => (
                <tr key={t.session_id}>
                  <td>
                    <Link
                      href={`/threads/${encodeURIComponent(t.session_id)}`}
                      className="mono"
                      title={t.session_id}
                    >
                      {t.session_id.length > 24
                        ? `${t.session_id.slice(0, 24)}…`
                        : t.session_id}
                    </Link>
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {t.turn_count.toLocaleString("en-US")}
                  </td>
                  <td>
                    <StatusPill status={t.last_status || "running"} />
                  </td>
                  <td
                    className="num"
                    style={{
                      textAlign: "right",
                      color:
                        t.error_count > 0 ? "var(--danger)" : "var(--text-3)",
                    }}
                  >
                    {t.error_count > 0 ? t.error_count : "—"}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {fmtLatency(t.latency_p95_ms)}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {t.total_tokens
                      ? t.total_tokens.toLocaleString("en-US")
                      : "—"}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {fmtCost(t.total_cost_usd)}
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {fmtRelative(t.last_run_at)}
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

// ---------------------------------------------------------------------------
// Empty / unconfigured states
// ---------------------------------------------------------------------------

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

function EmptyThreadsState({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>
        No sessions yet in <span className="mono">{project.slug}</span>.
      </h3>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          maxWidth: 640,
        }}
      >
        A session is a set of runs that share a{" "}
        <span className="mono">session_id</span>. Tag your runs with the same
        id across turns and they&apos;ll roll up here. Single-turn calls (no
        session_id) stay under <Link href="/runs">Traces</Link>.
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

// ---------------------------------------------------------------------------
// Badges + formatters
// ---------------------------------------------------------------------------

function KindBadge({ kind }: { kind: string }) {
  const k = (kind || "").toLowerCase();
  const cls =
    k === "llm"
      ? "kind-llm"
      : k === "tool"
        ? "kind-tool"
        : k === "retriever" || k === "retr" || k === "reranker"
          ? "kind-retr"
          : k === "agent"
            ? "kind-agent"
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

// Only null is "no data" — a true zero renders as 0 so the stats rail
// can't disguise an all-zero-token window as missing data.
function fmtTokens(n: number | null): string {
  if (n === null) return "—";
  if (n === 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}K`;
  return n.toLocaleString("en-US");
}

function fmtWindow(seconds: number): string {
  if (seconds < 3600) return `last ${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `last ${Math.round(seconds / 3600)}h`;
  return `last ${Math.round(seconds / 86400)}d`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}

function fmtRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
    return d.toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}
