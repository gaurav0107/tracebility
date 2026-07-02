import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  NewProjectButton,
  type WorkspaceOption,
} from "@/components/NewProjectButton";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";

/**
 * Overview — the post-signin home page.
 *
 * Architecture:
 *   1. Onboarding checklist (only when not all 4 surfaces have data)
 *   2. SDK quickstart (only when zero runs)
 *   3. Recent runs (the working surface)
 *   4. Metrics strip (compact summary, real time-range chips)
 *
 * Reads as a telemetry-first dashboard: runs front and centre, metrics
 * compressed into a horizontal strip. No fake CTAs, no fake controls.
 * Empty states teach the interface instead of leaving blank space.
 *
 * The time-range chips (1h / 6h / 24h / 7d) are REAL — they round-trip
 * through `?window=<seconds>`. Default is 1h.
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

interface MetricsResponse {
  window_seconds: number;
  runs: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  error_count: number;
  error_rate: number;
  total_tokens: number;
  total_cost_usd: number;
}

interface DatasetOut {
  id: string;
}
interface EvalRunOut {
  id: string;
}
interface PlaygroundList {
  items: Array<{ id: string }>;
}

const RANGES: { label: string; seconds: number }[] = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 6 * 3600 },
  { label: "24h", seconds: 24 * 3600 },
  { label: "7d", seconds: 7 * 24 * 3600 },
];
const DEFAULT_WINDOW = 3600;

function rangeFromQuery(v: string | undefined): number {
  if (!v) return DEFAULT_WINDOW;
  const n = Number(v);
  if (!Number.isFinite(n)) return DEFAULT_WINDOW;
  return RANGES.find((r) => r.seconds === n)?.seconds ?? DEFAULT_WINDOW;
}

function rangeLabel(seconds: number): string {
  return RANGES.find((r) => r.seconds === seconds)?.label ?? "1h";
}

export default async function OverviewPage({
  searchParams,
}: {
  searchParams?: { window?: string };
}) {
  const { active, all, reason } = await resolveActiveProject();
  const windowSeconds = rangeFromQuery(searchParams?.window);

  if (!active) {
    const wsRes = await apiGet<WorkspaceOption[]>("/v1/workspaces");
    const workspaces = wsRes.data ?? [];
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <PageHeader title="Overview" subtitle="no project resolved" />
          <UnconfiguredState reason={reason} workspaces={workspaces} />
        </PageInterior>
      </Shell>
    );
  }

  // Parallel fetch every signal we need so the home page is one round-trip
  // wide. Each apiGet falls back to null on 5xx; we degrade rather than fail.
  const [runsRes, metricsRes, datasetsRes, evalRunsRes, playgroundRes] =
    await Promise.all([
      apiGet<RunListResponse>(
        `/v1/runs?project_id=${encodeURIComponent(active.id)}&limit=50`,
      ),
      apiGet<MetricsResponse>(
        `/v1/metrics?project_id=${encodeURIComponent(active.id)}&window_seconds=${windowSeconds}`,
      ),
      apiGet<DatasetOut[]>(
        `/v1/datasets?project_id=${encodeURIComponent(active.id)}`,
      ),
      apiGet<EvalRunOut[]>(
        `/v1/eval-runs?project_id=${encodeURIComponent(active.id)}`,
      ),
      apiGet<PlaygroundList>(
        `/v1/playground/runs?project_id=${encodeURIComponent(active.id)}&limit=1`,
      ),
    ]);

  const runs = runsRes.data?.items ?? [];
  const metrics = metricsRes.data;

  const checklist: ChecklistState = {
    sentTrace: runs.length > 0,
    triedPlayground: (playgroundRes.data?.items?.length ?? 0) > 0,
    createdDataset: (datasetsRes.data?.length ?? 0) > 0,
    ranEval: (evalRunsRes.data?.length ?? 0) > 0,
  };
  const checklistComplete =
    checklist.sentTrace &&
    checklist.triedPlayground &&
    checklist.createdDataset &&
    checklist.ranEval;

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <PageHeader
          title="Overview"
          project={active}
          windowSeconds={windowSeconds}
        />

        {!checklistComplete ? (
          <OnboardingCard checklist={checklist} project={active} />
        ) : null}

        {runs.length === 0 ? (
          <FirstTraceQuickstart project={active} />
        ) : null}

        <RunsCard runs={runs} project={active} error={runsRes.error} />

        <MetricsStrip
          metrics={metrics}
          error={metricsRes.error}
          windowSeconds={windowSeconds}
        />
      </PageInterior>
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: 24,
        display: "grid",
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
  project,
  windowSeconds,
}: {
  title: string;
  subtitle?: string;
  project?: Project | null;
  windowSeconds?: number;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        gap: 16,
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1>{title}</h1>
        {project ? (
          <span
            className="mono"
            style={{ fontSize: 12, color: "var(--text-3)" }}
          >
            {project.slug}
          </span>
        ) : null}
        {subtitle ? (
          <span
            className="mono"
            style={{ fontSize: 12, color: "var(--text-3)" }}
          >
            {subtitle}
          </span>
        ) : null}
      </div>
      {project && windowSeconds !== undefined ? (
        <RangeChips windowSeconds={windowSeconds} />
      ) : null}
    </header>
  );
}

function RangeChips({ windowSeconds }: { windowSeconds: number }) {
  // Each chip is a server-side <Link> that round-trips ?window=<seconds>;
  // active state pulls from the current resolved windowSeconds.
  return (
    <nav
      aria-label="Time range"
      style={{ display: "flex", gap: 4, alignItems: "center" }}
    >
      {RANGES.map((r) => {
        const active = r.seconds === windowSeconds;
        return (
          <Link
            key={r.seconds}
            href={`/?window=${r.seconds}`}
            scroll={false}
            className="btn btn-sm btn-ghost"
            style={
              active
                ? {
                    background: "var(--surface-3)",
                    color: "var(--text)",
                    fontWeight: 500,
                    fontVariantNumeric: "tabular-nums",
                  }
                : { color: "var(--text-3)", fontVariantNumeric: "tabular-nums" }
            }
            aria-current={active ? "page" : undefined}
          >
            {r.label}
          </Link>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Onboarding checklist
// ---------------------------------------------------------------------------

interface ChecklistState {
  sentTrace: boolean;
  triedPlayground: boolean;
  createdDataset: boolean;
  ranEval: boolean;
}

function OnboardingCard({
  checklist,
  project,
}: {
  checklist: ChecklistState;
  project: Project;
}) {
  const items: {
    key: keyof ChecklistState;
    title: string;
    desc: string;
    href: string;
  }[] = [
    {
      key: "sentTrace",
      title: "Send your first trace",
      desc: "Use the curl example below or wire up the SDK.",
      href: "/runs",
    },
    {
      key: "triedPlayground",
      title: "Try the playground",
      desc: "Render a prompt against a model. Output writes a real trace.",
      href: "/playground",
    },
    {
      key: "createdDataset",
      title: "Create a dataset",
      desc: "Items you'll score evaluations against.",
      href: "/datasets",
    },
    {
      key: "ranEval",
      title: "Run an eval",
      desc: "Score a dataset with judges. PoLL or LLM-as-judge.",
      href: "/evals",
    },
  ];
  const done = items.filter((i) => checklist[i.key]).length;
  const total = items.length;

  return (
    <section
      className="card-section"
      aria-labelledby="onboarding-title"
      style={{ overflow: "hidden" }}
    >
      <header className="card-section-head">
        <div className="card-section-head-text">
          <h2 id="onboarding-title" className="card-section-title">
            Get started with{" "}
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {project.slug}
            </span>
          </h2>
          <p className="card-section-desc">
            Four things take you from empty workspace to real eval rigor.
            They unlock automatically as you use the product.
          </p>
        </div>
        <span
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--text-2)",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          {done} / {total}
        </span>
      </header>
      <div className="card-section-body-tight">
        <ol
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 12,
            margin: 0,
            padding: 0,
            listStyle: "none",
          }}
        >
          {items.map((item, idx) => (
            <ChecklistItem
              key={item.key}
              n={idx + 1}
              done={checklist[item.key]}
              title={item.title}
              desc={item.desc}
              href={item.href}
            />
          ))}
        </ol>
      </div>
    </section>
  );
}

function ChecklistItem({
  n,
  done,
  title,
  desc,
  href,
}: {
  n: number;
  done: boolean;
  title: string;
  desc: string;
  href: string;
}) {
  return (
    <li>
      <Link
        href={href}
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: 10,
          padding: 12,
          border: "1px solid var(--border)",
          borderRadius: "var(--r-2)",
          textDecoration: "none",
          color: "var(--text)",
          background: done ? "var(--surface-2)" : "var(--surface)",
          transition:
            "background var(--motion-fast) var(--ease-out), border-color var(--motion-fast) var(--ease-out)",
          minHeight: 76,
        }}
        className="onboarding-item"
      >
        <span
          aria-hidden
          style={{
            width: 20,
            height: 20,
            borderRadius: 9999,
            background: done ? "var(--accent)" : "transparent",
            border: done ? "none" : "1px solid var(--border-strong)",
            color: done ? "var(--accent-fg)" : "var(--text-3)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--f-mono)",
            fontSize: 11,
            fontWeight: 600,
            flexShrink: 0,
            marginTop: 2,
          }}
        >
          {done ? "✓" : n}
        </span>
        <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: done ? "var(--text-2)" : "var(--text)",
              textDecoration: done ? "line-through" : "none",
              textDecorationColor: "var(--text-3)",
              textDecorationThickness: "1px",
            }}
          >
            {title}
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-3)",
              lineHeight: 1.45,
            }}
          >
            {desc}
          </div>
        </div>
      </Link>
    </li>
  );
}

// ---------------------------------------------------------------------------
// First-trace quickstart (zero-state)
// ---------------------------------------------------------------------------

function FirstTraceQuickstart({ project }: { project: Project }) {
  // The curl is a literal copy-paste that works against the local ingest
  // service. project.slug fills in for visual relevance; the real ingest
  // resolves project by API key, which the user creates from /api-keys.
  const sample = `curl -X POST http://localhost:7080/v1/runs \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "sdk": "curl",
    "runs": [
      {
        "run_id": "'"$(uuidgen | tr "[:upper:]" "[:lower:]")"'",
        "name": "hello-world",
        "kind": "llm",
        "start_time": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
        "inputs": "{\\"prompt\\": \\"what is 2 + 2?\\"}",
        "outputs": "{\\"answer\\": \\"4\\"}"
      }
    ]
  }'`;
  return (
    <section className="card-section">
      <header className="card-section-head">
        <div className="card-section-head-text">
          <h2 className="card-section-title">Send your first trace</h2>
          <p className="card-section-desc">
            Run this from your terminal, or wire up the SDK in your code.
            Once a trace lands, it shows up in the table below.
          </p>
        </div>
        <Link href="/api-keys" className="btn btn-sm">
          Get an API key
        </Link>
      </header>
      <div className="card-section-body">
        <pre
          className="mono"
          style={{
            margin: 0,
            padding: 14,
            background: "var(--surface-3)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-2)",
            fontSize: 12,
            lineHeight: 1.6,
            color: "var(--text)",
            overflow: "auto",
          }}
          aria-label={`curl example for ${project.slug}`}
        >
          {sample}
        </pre>
      </div>
      <footer
        className="card-section-foot"
        style={{ fontSize: 12, color: "var(--text-3)" }}
      >
        <span>
          Or:{" "}
          <Link href="/playground" style={{ color: "var(--link)" }}>
            try the playground
          </Link>
          .
        </span>
        <Link
          href="https://github.com/langprobe/langprobe/blob/main/docs/getting-started.md"
          className="btn btn-sm btn-ghost"
        >
          Full docs →
        </Link>
      </footer>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Recent runs
// ---------------------------------------------------------------------------

function RunsCard({
  runs,
  project,
  error,
}: {
  runs: Run[];
  project: Project;
  error: string | null;
}) {
  return (
    <section className="card-section" style={{ overflow: "hidden" }}>
      <header className="card-section-head">
        <div className="card-section-head-text">
          <h2 className="card-section-title">Recent runs</h2>
          <p className="card-section-desc">
            Latest 50 traces in{" "}
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {project.slug}
            </span>
            . Click any row to inspect spans, prompts, and completions.
          </p>
        </div>
        <Link href="/runs" className="btn btn-sm btn-ghost">
          All runs →
        </Link>
      </header>
      {error ? (
        <div
          role="alert"
          style={{
            padding: "10px 16px",
            color: "var(--danger)",
            fontSize: 12,
            background: "var(--danger-soft)",
            borderTop: "1px solid var(--border)",
          }}
        >
          Couldn&apos;t load runs: {error}
        </div>
      ) : null}
      {runs.length === 0 ? (
        <EmptyRunsState project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
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
                  <td>
                    <Link href={`/runs/${r.run_id}`} className="mono">
                      {r.run_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td style={{ color: "var(--text)" }}>{r.name}</td>
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
                    {fmtInt(r.total_tokens)}
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

function EmptyRunsState({ project }: { project: Project }) {
  // Three reasons a run table can be empty: never-sent, sample-rate-zero,
  // or wrong-project-key. We surface all three so the user can diagnose
  // their own setup instead of staring at "no runs yet".
  return (
    <div
      className="card-section-body"
      style={{ display: "grid", gap: 14, padding: "28px 20px" }}
    >
      <div>
        <h3 style={{ marginBottom: 4 }}>
          No runs in{" "}
          <span className="mono" style={{ color: "var(--text-3)" }}>
            {project.slug}
          </span>
          .
        </h3>
        <p
          style={{
            margin: 0,
            color: "var(--text-2)",
            fontSize: 13,
            lineHeight: 1.55,
          }}
        >
          A run hasn&apos;t reached this project. Common reasons:
        </p>
      </div>
      <ul
        style={{
          margin: 0,
          paddingLeft: 18,
          color: "var(--text-2)",
          fontSize: 13,
          lineHeight: 1.7,
        }}
      >
        <li>
          The SDK isn&apos;t calling the ingest endpoint yet.{" "}
          <Link href="/api-keys" style={{ color: "var(--link)" }}>
            Get an API key
          </Link>{" "}
          and try the curl example above.
        </li>
        <li>
          The key is for a different project. Check{" "}
          <Link href="/api-keys" style={{ color: "var(--link)" }}>
            /api-keys
          </Link>
          .
        </li>
        <li>
          Sample rate is 0. See{" "}
          <Link href="/workspace" style={{ color: "var(--link)" }}>
            project settings
          </Link>
          .
        </li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Metrics strip
// ---------------------------------------------------------------------------

function MetricsStrip({
  metrics,
  error,
  windowSeconds,
}: {
  metrics: MetricsResponse | null | undefined;
  error: string | null;
  windowSeconds: number;
}) {
  // The metrics strip is intentionally compressed into one horizontal row,
  // not a 6-tile grid. Reads as "IDE bottom bar" rather than "SaaS hero".
  // Vertical alignment is the grid here; values use mono numbers so the
  // strip doesn't shift width as data updates.
  if (error) {
    return (
      <section
        className="card-section"
        role="alert"
        style={{ overflow: "hidden" }}
      >
        <div
          className="card-section-body-tight"
          style={{
            color: "var(--danger)",
            fontSize: 12,
            background: "var(--danger-soft)",
          }}
        >
          Couldn&apos;t load metrics: {error}
        </div>
      </section>
    );
  }

  const items = [
    {
      label: "Runs",
      value: metrics ? fmtInt(metrics.runs) : "—",
    },
    {
      label: "Errors",
      value: metrics
        ? `${fmtInt(metrics.error_count)} (${(metrics.error_rate * 100).toFixed(1)}%)`
        : "—",
      tone:
        metrics && metrics.error_rate >= 0.05
          ? ("danger" as const)
          : undefined,
    },
    {
      label: "p50",
      value: fmtMs(metrics?.p50_ms ?? null),
    },
    {
      label: "p95",
      value: fmtMs(metrics?.p95_ms ?? null),
    },
    {
      label: "p99",
      value: fmtMs(metrics?.p99_ms ?? null),
    },
    {
      label: "Tokens",
      value: metrics ? fmtInt(metrics.total_tokens) : "—",
    },
    {
      label: "Cost",
      value: metrics ? fmtCostTotal(metrics.total_cost_usd) : "—",
    },
  ];

  return (
    <section
      aria-label={`Project metrics, last ${rangeLabel(windowSeconds)}`}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))`,
        gap: 0,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-3)",
        boxShadow: "inset 0 1px 0 rgba(255, 255, 255, 0.6), var(--shadow-1)",
        overflow: "hidden",
      }}
    >
      {items.map((it, i) => (
        <div
          key={it.label}
          style={{
            padding: "12px 16px",
            display: "grid",
            gap: 4,
            borderLeft: i === 0 ? "none" : "1px solid var(--border)",
            minWidth: 0,
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-3)",
            }}
          >
            {it.label}
          </span>
          <span
            className="mono"
            style={{
              fontSize: 18,
              fontWeight: 500,
              letterSpacing: "-0.01em",
              color: it.tone === "danger" ? "var(--danger)" : "var(--text)",
              fontVariantNumeric: "tabular-nums",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={it.value}
          >
            {it.value}
          </span>
        </div>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Pre-project state
// ---------------------------------------------------------------------------

function UnconfiguredState({
  reason,
  workspaces,
}: {
  reason: string | null;
  workspaces: WorkspaceOption[];
}) {
  const isNotAuth = reason === "not authenticated";
  const hasWorkspace = workspaces.length > 0;

  return (
    <section className="card-empty" style={{ maxWidth: 720, width: "100%" }}>
      <span className="card-empty-icon" aria-hidden>
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 4v16M4 12h16" />
        </svg>
      </span>
      <h2 className="card-empty-title">
        {isNotAuth
          ? "Create your account"
          : hasWorkspace
            ? "Create your first project"
            : "Run the setup wizard"}
      </h2>
      <p className="card-empty-desc">
        {isNotAuth ? (
          <>
            Sign up with Google or GitHub. We auto-provision a personal
            workspace and a first project so you can start sending traces in
            minutes.
          </>
        ) : hasWorkspace ? (
          <>
            A project is the unit of tenancy. Every trace, eval, and
            dataset belongs to one. Create one to start ingesting data;
            you can add more later from{" "}
            <Link href="/workspace" style={{ color: "var(--link)" }}>
              workspace settings
            </Link>
            .
          </>
        ) : (
          <>
            Run the setup wizard to create the root account and workspace.
            See the{" "}
            <a
              href="https://github.com/langprobe/langprobe/blob/main/docs/getting-started.md"
              style={{ color: "var(--link)" }}
            >
              getting-started doc
            </a>
            .
          </>
        )}
      </p>
      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
        {hasWorkspace && !isNotAuth ? (
          <NewProjectButton workspaces={workspaces} variant="empty-state" />
        ) : null}
        {isNotAuth ? (
          <>
            <Link href="/login?tab=signup" className="btn btn-primary">
              Create account
            </Link>
            <Link href="/login" className="btn btn-ghost">
              Sign in
            </Link>
          </>
        ) : null}
      </div>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 6, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cell renderers
// ---------------------------------------------------------------------------

function KindBadge({ kind }: { kind: string }) {
  const k = kind.toLowerCase();
  const cls =
    k === "llm"
      ? "kind-llm"
      : k === "tool"
        ? "kind-tool"
        : k === "retriever" || k === "retr"
          ? "kind-retr"
          : "kind-chain";
  return <span className={`kind-badge ${cls}`}>{kind}</span>;
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

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function fmtLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

function fmtCost(usd: number): string {
  if (!usd) return "—";
  return `$${usd.toFixed(4)}`;
}

function fmtCostTotal(usd: number): string {
  if (!usd) return "$0.00";
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}
