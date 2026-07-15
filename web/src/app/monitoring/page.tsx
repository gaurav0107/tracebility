import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  type AlertEventRow,
  type AlertRuleRow,
  NewAlertRuleButton,
} from "@/components/AlertsClient";
import {
  SavedViewsBar,
  type SavedViewRow,
} from "@/components/SavedViewsClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject, type Project } from "@/lib/projects";
import { AlertsPanel } from "./AlertsPanel";

/**
 * Monitoring — one surface, two tabs (LangSmith-style).
 *
 *   - Dashboards (default): four inline-SVG charts (latency
 *     p50/p95/p99, runs, error rate, cost) over the last N seconds,
 *     plus a model breakdown table. Window: 1h/6h/24h/7d via
 *     querystring `window`.
 *   - Alerts (?tab=alerts): rules + incident history over the same
 *     ClickHouse rollups the charts read. Kept on this page so the
 *     "metric looks bad → set a threshold on it" loop is one click,
 *     not a navigation.
 *
 * Rules are fetched on both tabs — the Alerts tab label carries a
 * firing count so an open incident is visible from the charts.
 */

export const dynamic = "force-dynamic";

interface TimeseriesBucket {
  bucket_start: string;
  runs: number;
  error_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  total_tokens: number;
  total_cost_usd: number;
}

interface TimeseriesResponse {
  window_seconds: number;
  bucket_seconds: number;
  buckets: TimeseriesBucket[];
}

interface ModelBreakdownItem {
  model: string;
  runs: number;
  error_count: number;
  p95_ms: number | null;
  total_tokens: number;
  total_cost_usd: number;
}

interface ModelBreakdownResponse {
  items: ModelBreakdownItem[];
}

const WINDOWS: { label: string; seconds: number; bucket: number }[] = [
  { label: "1h", seconds: 3600, bucket: 60 },
  { label: "6h", seconds: 6 * 3600, bucket: 5 * 60 },
  { label: "24h", seconds: 24 * 3600, bucket: 15 * 60 },
  { label: "7d", seconds: 7 * 24 * 3600, bucket: 60 * 60 },
];

type MonitoringTab = "dashboards" | "alerts";

interface AlertEventListWire {
  events: AlertEventRow[];
}

export default async function MonitoringPage({
  searchParams,
}: {
  searchParams: { window?: string; model?: string; kind?: string; tab?: string };
}) {
  const { active, all, reason } = await resolveActiveProject();
  const tab: MonitoringTab =
    searchParams.tab === "alerts" ? "alerts" : "dashboards";
  const win =
    WINDOWS.find((w) => w.label === searchParams.window) ?? WINDOWS[2];
  const modelFilter = (searchParams.model || "").trim() || null;
  const crumbs = (
    <>
      {active ? <span className="mono">{active.slug}</span> : null}
      <span className="sep">/</span>
      <span>monitoring</span>
      <span className="sep">/</span>
      <span className="last">{tab}</span>
    </>
  );

  if (!active) {
    return (
      <Shell active={null} projects={all} crumbs={crumbs}>
        <PageInterior>
          <PageHeader title="Monitoring" subtitle="dashboards + alerts" />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  // Rules power the Alerts tab and the firing badge on both tabs. Start
  // the fetch unawaited so it overlaps each tab's own queries instead of
  // adding a serial round-trip in front of them. Safe to float only
  // because apiGet never rejects (it catches and returns {error}) — keep
  // that invariant or add a .catch here.
  const rulesPromise = apiGet<AlertRuleRow[]>(
    `/v1/alerts?project_id=${encodeURIComponent(active.id)}`,
  );

  if (tab === "alerts") {
    const [rulesRes, eventsRes] = await Promise.all([
      rulesPromise,
      apiGet<AlertEventListWire>(
        `/v1/alerts/events?project_id=${encodeURIComponent(active.id)}&limit=200`,
      ),
    ]);
    const rules = rulesRes.data ?? [];
    const firing = rules.filter((r) => r.open_incident_id).length;
    const events = eventsRes.data?.events ?? [];
    return (
      <Shell active={active} projects={all} crumbs={crumbs}>
        <PageInterior>
          <PageHeader
            title="Monitoring"
            subtitle={`${active.slug} · ${rules.length} ${rules.length === 1 ? "rule" : "rules"}`}
            right={<NewAlertRuleButton projectId={active.id} />}
          />
          <TabBar tab={tab} firing={firing} rulesError={rulesRes.error} windowLabel={win.label} model={modelFilter} />
          <AlertsPanel
            rules={rules}
            events={events}
            rulesError={rulesRes.error}
            eventsError={eventsRes.error}
            project={active}
          />
        </PageInterior>
      </Shell>
    );
  }

  const [rulesRes, tsRes, byModelRes, viewsRes] = await Promise.all([
    rulesPromise,
    apiGet<TimeseriesResponse>(
      `/v1/metrics/timeseries?project_id=${encodeURIComponent(active.id)}&window_seconds=${win.seconds}&bucket_seconds=${win.bucket}`,
    ),
    apiGet<ModelBreakdownResponse>(
      `/v1/metrics/by-model?project_id=${encodeURIComponent(active.id)}&window_seconds=${win.seconds}`,
    ),
    apiGet<SavedViewRow[]>(
      `/v1/saved-views?project_id=${encodeURIComponent(active.id)}&surface=monitoring`,
    ),
  ]);
  const rules = rulesRes.data ?? [];
  const firing = rules.filter((r) => r.open_incident_id).length;
  const buckets = tsRes.data?.buckets ?? [];
  const allModels = byModelRes.data?.items ?? [];
  // Apply the optional model filter at the view layer; the breakdown
  // endpoint is unchanged so the saved view round-trips cleanly when
  // we extend that endpoint with a server-side filter later.
  const models = modelFilter
    ? allModels.filter((m) => m.model === modelFilter)
    : allModels;
  const views = viewsRes.data ?? [];

  const totals = aggregate(buckets);

  return (
    <Shell active={active} projects={all} crumbs={crumbs}>
      <PageInterior>
        <PageHeader
          title="Monitoring"
          subtitle={`${active.slug} · last ${win.label}${modelFilter ? ` · ${modelFilter}` : ""}`}
          right={<WindowPicker current={win.label} model={modelFilter} />}
        />
        <TabBar tab={tab} firing={firing} rulesError={rulesRes.error} windowLabel={win.label} model={modelFilter} />
        <SavedViewsBar
          projectId={active.id}
          views={views}
          surface="monitoring"
          basePath="/monitoring"
        />
        <KpiStrip
          totals={totals}
          unavailable={Boolean(tsRes.error) && buckets.length === 0}
        />
        <ChartGrid buckets={buckets} bucketSeconds={win.bucket} />
        <ModelTable models={models} reason={byModelRes.error} />
        {buckets.length === 0 ? (
          <EmptyHint reason={tsRes.error} project={active} />
        ) : null}
      </PageInterior>
    </Shell>
  );
}

function TabBar({
  tab,
  firing,
  rulesError,
  windowLabel,
  model,
}: {
  tab: MonitoringTab;
  firing: number;
  rulesError: string | null;
  windowLabel?: string;
  model?: string | null;
}) {
  // Keep the dashboard context (window, model) across the tab round-trip —
  // "metric looks bad → set a threshold → back to the chart" must not
  // reset to the default window.
  const params = new URLSearchParams();
  if (windowLabel && windowLabel !== WINDOWS[0].label) {
    params.set("window", windowLabel);
  }
  if (model) params.set("model", model);
  const dashboardsQs = params.toString();
  const alertsParams = new URLSearchParams(params);
  alertsParams.set("tab", "alerts");
  return (
    <nav className="seg" aria-label="Monitoring tabs" style={{ alignSelf: "flex-start" }}>
      <Link
        href={dashboardsQs ? `/monitoring?${dashboardsQs}` : "/monitoring"}
        className={`seg-item${tab === "dashboards" ? " active" : ""}`}
        aria-current={tab === "dashboards" ? "page" : undefined}
      >
        Dashboards
      </Link>
      <Link
        href={`/monitoring?${alertsParams.toString()}`}
        className={`seg-item${tab === "alerts" ? " active" : ""}`}
        aria-current={tab === "alerts" ? "page" : undefined}
      >
        Alerts
        {firing > 0 ? (
          <span
            className="seg-count"
            style={{ color: "var(--danger)", fontWeight: 700 }}
          >
            {firing} firing
          </span>
        ) : rulesError ? (
          // Rules fetch failed — "no badge" must not read as "nothing firing".
          <span className="seg-count" title={`alert rules unavailable (${rulesError})`}>
            ?
          </span>
        ) : null}
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
        gap: 20,
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

function WindowPicker({
  current,
  model,
}: {
  current: string;
  model?: string | null;
}) {
  return (
    <div
      style={{
        display: "inline-flex",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-2)",
        overflow: "hidden",
      }}
    >
      {WINDOWS.map((w) => (
        <Link
          key={w.label}
          href={`/monitoring?window=${w.label}${model ? `&model=${encodeURIComponent(model)}` : ""}`}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            fontFamily: "var(--font-mono)",
            color: w.label === current ? "var(--text)" : "var(--text-3)",
            background:
              w.label === current ? "var(--surface-3)" : "transparent",
            borderRight: "1px solid var(--border)",
            textDecoration: "none",
          }}
        >
          {w.label}
        </Link>
      ))}
    </div>
  );
}

function aggregate(buckets: TimeseriesBucket[]) {
  let runs = 0;
  let errors = 0;
  let tokens = 0;
  let cost = 0;
  let p95Sum = 0;
  let p95Count = 0;
  for (const b of buckets) {
    runs += b.runs;
    errors += b.error_count;
    tokens += b.total_tokens;
    cost += b.total_cost_usd;
    if (b.p95_ms !== null) {
      p95Sum += b.p95_ms;
      p95Count++;
    }
  }
  return {
    runs,
    errors,
    tokens,
    cost,
    errorRate: runs > 0 ? errors / runs : null,
    p95Avg: p95Count > 0 ? p95Sum / p95Count : null,
  };
}

function KpiStrip({
  totals,
  unavailable = false,
}: {
  totals: ReturnType<typeof aggregate>;
  unavailable?: boolean;
}) {
  // A failed timeseries fetch must not read as "0 runs, 0 errors, healthy".
  if (unavailable) {
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
        {["Runs", "Errors", "Error rate", "p95 latency", "Total cost"].map(
          (label, i) => (
            <KpiCell key={label} label={label} value="—" last={i === 4} />
          ),
        )}
      </section>
    );
  }
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
      <KpiCell label="Runs" value={totals.runs.toLocaleString("en-US")} />
      <KpiCell
        label="Errors"
        value={totals.errors.toLocaleString("en-US")}
        color={totals.errors > 0 ? "var(--danger)" : undefined}
      />
      <KpiCell
        label="Error rate"
        value={
          totals.errorRate === null
            ? "—"
            : `${(totals.errorRate * 100).toFixed(2)}%`
        }
        color={
          totals.errorRate !== null && totals.errorRate > 0.01
            ? "var(--danger)"
            : undefined
        }
      />
      <KpiCell
        label="p95 latency"
        value={
          totals.p95Avg === null
            ? "—"
            : totals.p95Avg < 1000
              ? `${Math.round(totals.p95Avg)} ms`
              : `${(totals.p95Avg / 1000).toFixed(2)} s`
        }
      />
      <KpiCell label="Total cost" value={fmtCost(totals.cost)} last />
    </section>
  );
}

function KpiCell({
  label,
  value,
  color,
  last = false,
}: {
  label: string;
  value: string;
  color?: string;
  last?: boolean;
}) {
  return (
    <div
      style={{
        padding: "16px 20px",
        borderRight: last ? undefined : "1px solid var(--border)",
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

function ChartGrid({
  buckets,
  bucketSeconds,
}: {
  buckets: TimeseriesBucket[];
  bucketSeconds: number;
}) {
  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
        gap: 16,
      }}
    >
      <ChartCard
        title="Latency"
        subtitle="p50 / p95 / p99 (ms)"
        chart={
          <MultiLineChart
            buckets={buckets}
            bucketSeconds={bucketSeconds}
            series={[
              { key: "p50_ms", color: "var(--accent)", label: "p50" },
              { key: "p95_ms", color: "var(--text)", label: "p95" },
              { key: "p99_ms", color: "var(--danger)", label: "p99" },
            ]}
            yFormat={(v) =>
              v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`
            }
          />
        }
      />
      <ChartCard
        title="Throughput"
        subtitle="runs per bucket"
        chart={
          <BarChart
            buckets={buckets}
            bucketSeconds={bucketSeconds}
            valueKey="runs"
            color="var(--accent)"
          />
        }
      />
      <ChartCard
        title="Errors"
        subtitle="error count per bucket"
        chart={
          <BarChart
            buckets={buckets}
            bucketSeconds={bucketSeconds}
            valueKey="error_count"
            color="var(--danger)"
          />
        }
      />
      <ChartCard
        title="Cost"
        subtitle="USD per bucket"
        chart={
          <BarChart
            buckets={buckets}
            bucketSeconds={bucketSeconds}
            valueKey="total_cost_usd"
            color="var(--link)"
            yFormat={(v) => `$${v.toFixed(4)}`}
          />
        }
      />
    </section>
  );
}

function ChartCard({
  title,
  subtitle,
  chart,
}: {
  title: string;
  subtitle: string;
  chart: React.ReactNode;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>{title}</h2>
          <span className="card-sub">{subtitle}</span>
        </div>
      </div>
      <div style={{ padding: 16 }}>{chart}</div>
    </section>
  );
}

interface SeriesSpec {
  key: "p50_ms" | "p95_ms" | "p99_ms";
  color: string;
  label: string;
}

function MultiLineChart({
  buckets,
  bucketSeconds: _bucketSeconds,
  series,
  yFormat,
}: {
  buckets: TimeseriesBucket[];
  bucketSeconds: number;
  series: SeriesSpec[];
  yFormat?: (v: number) => string;
}) {
  const W = 600;
  const H = 180;
  const PAD = { top: 8, right: 8, bottom: 24, left: 44 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  if (buckets.length === 0) {
    return <EmptyChart />;
  }

  const allValues: number[] = [];
  for (const b of buckets) {
    for (const s of series) {
      const v = b[s.key];
      if (v !== null) allValues.push(v);
    }
  }
  const yMax = allValues.length > 0 ? Math.max(...allValues) : 1;
  const yNice = niceCeil(yMax);

  const x = (i: number) =>
    PAD.left + (i / Math.max(buckets.length - 1, 1)) * innerW;
  const y = (v: number) => PAD.top + innerH - (v / yNice) * innerH;

  function pathFor(key: SeriesSpec["key"]): string {
    let d = "";
    let started = false;
    buckets.forEach((b, i) => {
      const v = b[key];
      if (v === null) {
        started = false;
        return;
      }
      const cmd = started ? "L" : "M";
      d += `${cmd}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      started = true;
    });
    return d.trim();
  }

  const ticks = [0, yNice / 2, yNice];

  return (
    <ChartFrame W={W} H={H}>
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={y(t)}
            y2={y(t)}
            stroke="var(--border)"
            strokeDasharray={t === 0 ? "" : "2,3"}
          />
          <text
            x={PAD.left - 6}
            y={y(t)}
            fontSize="10"
            fontFamily="var(--font-mono)"
            fill="var(--text-3)"
            textAnchor="end"
            dominantBaseline="middle"
          >
            {yFormat ? yFormat(t) : t.toFixed(0)}
          </text>
        </g>
      ))}
      {series.map((s) => {
        const pts = buckets
          .map((b, i) => ({ v: b[s.key], i }))
          .filter((p): p is { v: number; i: number } => p.v !== null);
        return (
          <g key={s.key}>
            <path
              d={pathFor(s.key)}
              fill="none"
              stroke={s.color}
              strokeWidth="1.5"
            />
            {pts.length < 3
              ? pts.map((p) => (
                  <circle
                    key={p.i}
                    cx={x(p.i)}
                    cy={y(p.v)}
                    r="3"
                    fill={s.color}
                  />
                ))
              : null}
          </g>
        );
      })}
      <XAxisLabels buckets={buckets} W={W} H={H} PAD={PAD} />
      <Legend series={series} W={W} />
    </ChartFrame>
  );
}

function BarChart({
  buckets,
  bucketSeconds: _bucketSeconds,
  valueKey,
  color,
  yFormat,
}: {
  buckets: TimeseriesBucket[];
  bucketSeconds: number;
  valueKey: "runs" | "error_count" | "total_cost_usd";
  color: string;
  yFormat?: (v: number) => string;
}) {
  const W = 600;
  const H = 180;
  const PAD = { top: 8, right: 8, bottom: 24, left: 44 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  if (buckets.length === 0) {
    return <EmptyChart />;
  }

  const values = buckets.map((b) => Number(b[valueKey] ?? 0));
  const yMax = Math.max(...values, 1);
  const yNice = niceCeil(yMax);

  const barWidth = Math.max(innerW / buckets.length - 2, 1);
  const xStep = innerW / buckets.length;
  const y = (v: number) => PAD.top + innerH - (v / yNice) * innerH;

  const ticks = [0, yNice / 2, yNice];
  const fmt = yFormat ?? ((v: number) => v.toFixed(0));

  return (
    <ChartFrame W={W} H={H}>
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={y(t)}
            y2={y(t)}
            stroke="var(--border)"
            strokeDasharray={t === 0 ? "" : "2,3"}
          />
          <text
            x={PAD.left - 6}
            y={y(t)}
            fontSize="10"
            fontFamily="var(--font-mono)"
            fill="var(--text-3)"
            textAnchor="end"
            dominantBaseline="middle"
          >
            {fmt(t)}
          </text>
        </g>
      ))}
      {values.map((v, i) => {
        const h = (v / yNice) * innerH;
        return (
          <rect
            key={i}
            x={PAD.left + i * xStep + 1}
            y={PAD.top + innerH - h}
            width={barWidth}
            height={h}
            fill={color}
            opacity={v === 0 ? 0.15 : 0.85}
          />
        );
      })}
      <XAxisLabels buckets={buckets} W={W} H={H} PAD={PAD} />
    </ChartFrame>
  );
}

function ChartFrame({
  W,
  H,
  children,
}: {
  W: number;
  H: number;
  children: React.ReactNode;
}) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      style={{ display: "block" }}
    >
      {children}
    </svg>
  );
}

function XAxisLabels({
  buckets,
  W,
  H,
  PAD,
}: {
  buckets: TimeseriesBucket[];
  W: number;
  H: number;
  PAD: { top: number; right: number; bottom: number; left: number };
}) {
  if (buckets.length === 0) return null;
  const innerW = W - PAD.left - PAD.right;
  const idxs = [0, Math.floor(buckets.length / 2), buckets.length - 1];
  return (
    <>
      {idxs.map((i) => {
        const t = buckets[i]?.bucket_start;
        if (!t) return null;
        const x =
          PAD.left + (i / Math.max(buckets.length - 1, 1)) * innerW;
        return (
          <text
            key={i}
            x={x}
            y={H - 6}
            fontSize="10"
            fontFamily="var(--font-mono)"
            fill="var(--text-3)"
            textAnchor={i === 0 ? "start" : i === buckets.length - 1 ? "end" : "middle"}
          >
            {fmtAxisTime(t)}
          </text>
        );
      })}
    </>
  );
}

function Legend({ series, W }: { series: SeriesSpec[]; W: number }) {
  const itemW = 56;
  const total = series.length * itemW;
  return (
    <g transform={`translate(${W - total - 12}, 12)`}>
      {series.map((s, i) => (
        <g key={s.key} transform={`translate(${i * itemW}, 0)`}>
          <line
            x1={0}
            x2={12}
            y1={4}
            y2={4}
            stroke={s.color}
            strokeWidth="1.5"
          />
          <text
            x={16}
            y={4}
            fontSize="10"
            fontFamily="var(--font-mono)"
            fill="var(--text-2)"
            dominantBaseline="middle"
          >
            {s.label}
          </text>
        </g>
      ))}
    </g>
  );
}

function EmptyChart() {
  return (
    <div
      style={{
        height: 180,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-3)",
        fontSize: 12,
      }}
    >
      No data in window.
    </div>
  );
}

function ModelTable({
  models,
  reason,
}: {
  models: ModelBreakdownItem[];
  reason: string | null;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>By model</h2>
          <span className="card-sub">LLM spans only</span>
        </div>
      </div>
      {models.length === 0 ? (
        <div style={{ padding: 24, color: "var(--text-3)", fontSize: 13 }}>
          No LLM spans recorded in this window.
          {reason ? (
            <span
              className="mono"
              style={{ marginLeft: 8, fontSize: 11 }}
            >
              ({reason})
            </span>
          ) : null}
        </div>
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Model</th>
                <th style={{ textAlign: "right" }}>Calls</th>
                <th style={{ textAlign: "right" }}>Errors</th>
                <th style={{ textAlign: "right" }}>p95 latency</th>
                <th style={{ textAlign: "right" }}>Tokens</th>
                <th style={{ textAlign: "right" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.model}>
                  <td className="mono">{m.model}</td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {m.runs.toLocaleString("en-US")}
                  </td>
                  <td
                    className="num"
                    style={{
                      textAlign: "right",
                      color:
                        m.error_count > 0
                          ? "var(--danger)"
                          : "var(--text-3)",
                    }}
                  >
                    {m.error_count > 0 ? m.error_count : "—"}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {m.p95_ms === null
                      ? "—"
                      : m.p95_ms < 1000
                        ? `${Math.round(m.p95_ms)} ms`
                        : `${(m.p95_ms / 1000).toFixed(2)} s`}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {m.total_tokens.toLocaleString("en-US")}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {fmtCost(m.total_cost_usd)}
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

function EmptyHint({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div className="card card-pad-lg">
      <h3 style={{ marginBottom: 6 }}>
        No runs in <span className="mono">{project.slug}</span> for this
        window.
      </h3>
      <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
        Send a trace, then refresh.
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
        Run the setup wizard or create a project before viewing dashboards.
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

function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / exp;
  let nice: number;
  if (m <= 1) nice = 1;
  else if (m <= 2) nice = 2;
  else if (m <= 5) nice = 5;
  else nice = 10;
  return nice * exp;
}

function fmtCost(usd: number): string {
  if (!usd) return "—";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 100) return `$${usd.toFixed(2)}`;
  return `$${Math.round(usd).toLocaleString("en-US")}`;
}

function fmtAxisTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toISOString().slice(11, 16);
  } catch {
    return iso;
  }
}
