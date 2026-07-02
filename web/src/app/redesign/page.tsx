import {
  Activity,
  Bell,
  Boxes,
  ChevronDown,
  Command,
  Database,
  FlaskConical,
  Gauge,
  GitCompare,
  Home,
  KeyRound,
  Library,
  LineChart,
  MessageSquare,
  Play,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
  Zap,
} from "lucide-react";
import Link from "next/link";
import styles from "./page.module.css";

const navGroups = [
  {
    label: "Observe",
    items: [
      { label: "Overview", icon: Home, active: true },
      { label: "Traces", icon: Activity },
      { label: "Threads", icon: MessageSquare },
      { label: "Monitoring", icon: LineChart },
      { label: "Alerts", icon: Bell },
      { label: "Replay", icon: Sparkles },
    ],
  },
  {
    label: "Improve",
    items: [
      { label: "Evals", icon: FlaskConical },
      { label: "Comparisons", icon: GitCompare },
      { label: "Datasets", icon: Database },
      { label: "Prompts", icon: Library },
      { label: "Playground", icon: Play },
      { label: "Studio", icon: Boxes },
    ],
  },
  {
    label: "Control",
    items: [
      { label: "API keys", icon: KeyRound },
      { label: "Members", icon: UserRound },
      { label: "Workspace", icon: Settings },
    ],
  },
];

const kpis = [
  { label: "Runs", value: "48.2k", delta: "+12.4%" },
  { label: "p95 latency", value: "842ms", delta: "-7.1%" },
  { label: "Error rate", value: "0.42%", delta: "+0.08%", warn: true },
  { label: "Eval pass", value: "93.8%", delta: "+3.2%" },
];

const traces = [
  { name: "agent.plan", width: 88, time: "422ms", kind: "chain" },
  { name: "retriever.search", width: 44, time: "117ms", kind: "tool" },
  { name: "llm.reasoning", width: 76, time: "381ms", kind: "llm" },
  { name: "tool.lookup_account", width: 31, time: "86ms", kind: "tool" },
  { name: "llm.final", width: 58, time: "196ms", kind: "llm" },
];

const runs = [
  {
    id: "r_8df2a91c",
    name: "support-agent/escalation",
    kind: "llm",
    status: "ok",
    latency: "842ms",
    tokens: "2,914",
    cost: "$0.019",
    started: "14:32:08",
  },
  {
    id: "r_19aa44e2",
    name: "retrieval/contract-summary",
    kind: "tool",
    status: "ok",
    latency: "286ms",
    tokens: "1,128",
    cost: "$0.006",
    started: "14:31:46",
  },
  {
    id: "r_72c9f0ba",
    name: "agent/refund-policy",
    kind: "chain",
    status: "error",
    latency: "1.4s",
    tokens: "3,506",
    cost: "$0.028",
    started: "14:30:12",
  },
  {
    id: "r_43df0d7e",
    name: "playground/gpt-4.1-mini",
    kind: "llm",
    status: "running",
    latency: "live",
    tokens: "829",
    cost: "$0.004",
    started: "14:29:58",
  },
  {
    id: "r_110ca4b9",
    name: "batch/eval-nightly",
    kind: "chain",
    status: "ok",
    latency: "632ms",
    tokens: "1,902",
    cost: "$0.014",
    started: "14:28:33",
  },
];

const queue = [
  { title: "Refund policy judge", meta: "poLL panel · 412 pending", score: "94.1" },
  { title: "Long-context drift", meta: "dataset · nightly-prod", score: "88.7" },
  { title: "Tool selection", meta: "annotation queue · 37 open", score: "91.4" },
];

const spark = [
  22, 28, 18, 31, 42, 34, 39, 45, 51, 47, 62, 58, 74, 68, 55, 61, 72, 66, 82,
  76, 69, 63, 57, 49,
];

export default function RedesignPreviewPage() {
  return (
    <div className={styles.preview}>
      <div className={styles.shell}>
        <PreviewSidebar />
        <main className={styles.main}>
          <Topbar />
          <div className={styles.content}>
            <header className={styles.pageHead}>
              <div className={styles.titleBlock}>
                <span className={styles.eyebrow}>Redesign preview</span>
                <h1 className={styles.title}>Production agent telemetry</h1>
                <p className={styles.subtitle}>
                  A denser, calmer overview for traces, eval health, cost, and
                  queue pressure across the active project.
                </p>
              </div>
              <div className={styles.actions}>
                <button className={`btn ${styles.iconButton}`} aria-label="Alerts">
                  <Bell size={15} strokeWidth={1.7} />
                </button>
                <button className={`btn ${styles.iconButton}`} aria-label="Command palette">
                  <Command size={15} strokeWidth={1.7} />
                </button>
                <button className={`btn btn-primary ${styles.primaryAction}`}>
                  <Zap size={15} strokeWidth={1.8} />
                  New trace
                </button>
              </div>
            </header>

            <section className={styles.kpiGrid} aria-label="Project metrics">
              {kpis.map((kpi) => (
                <article className={styles.kpi} key={kpi.label}>
                  <div className={styles.kpiTop}>
                    <span>{kpi.label}</span>
                    {kpi.warn ? (
                      <Gauge size={15} strokeWidth={1.6} />
                    ) : (
                      <ShieldCheck size={15} strokeWidth={1.6} />
                    )}
                  </div>
                  <div className={styles.kpiValue}>{kpi.value}</div>
                  <div
                    className={`${styles.kpiDelta} ${kpi.warn ? styles.kpiWarn : ""}`}
                  >
                    {kpi.delta} vs prior 24h
                  </div>
                </article>
              ))}
            </section>

            <div className={styles.dashboardGrid}>
              <div className={styles.leftStack}>
                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Trace latency map</h2>
                      <p className={styles.panelDesc}>
                        Selected run breakdown by span duration.
                      </p>
                    </div>
                    <div className={styles.rangeTabs} aria-label="Time range">
                      <button className={`${styles.tab} ${styles.tabActive}`}>1h</button>
                      <button className={styles.tab}>6h</button>
                      <button className={styles.tab}>24h</button>
                    </div>
                  </div>
                  <div className={styles.traceCanvas}>
                    {traces.map((trace) => (
                      <div className={styles.traceRow} key={trace.name}>
                        <div className={styles.traceLabel}>
                          <span className={kindClass(trace.kind)}>{trace.kind}</span>
                          <span className={styles.traceName}>{trace.name}</span>
                        </div>
                        <div className={styles.traceBarTrack}>
                          <span
                            className={styles.traceBar}
                            style={{ width: `${trace.width}%` }}
                          />
                        </div>
                        <div className={styles.traceTime}>{trace.time}</div>
                      </div>
                    ))}
                  </div>
                </section>

                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Recent runs</h2>
                      <p className={styles.panelDesc}>
                        Latest production traces with cost and latency context.
                      </p>
                    </div>
                    <Link href="/runs" className="btn btn-sm btn-ghost">
                      Open traces
                    </Link>
                  </div>
                  <div className={styles.tableWrap}>
                    <table className={styles.table}>
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Name</th>
                          <th>Kind</th>
                          <th>Status</th>
                          <th className={styles.num}>Latency</th>
                          <th className={styles.num}>Tokens</th>
                          <th className={styles.num}>Cost</th>
                          <th className={styles.num}>Started</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((run) => (
                          <tr key={run.id}>
                            <td className={styles.runId}>{run.id.slice(0, 10)}</td>
                            <td>{run.name}</td>
                            <td>
                              <span className={kindClass(run.kind)}>{run.kind}</span>
                            </td>
                            <td>
                              <span className={styles.status}>
                                <span
                                  className={`${styles.dot} ${
                                    run.status === "error"
                                      ? styles.dotDanger
                                      : run.status === "running"
                                        ? styles.dotInfo
                                        : ""
                                  }`}
                                />
                                {run.status}
                              </span>
                            </td>
                            <td className={styles.num}>{run.latency}</td>
                            <td className={styles.num}>{run.tokens}</td>
                            <td className={styles.num}>{run.cost}</td>
                            <td className={styles.num}>{run.started}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>

              <aside className={styles.rightStack} aria-label="Project side panels">
                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Eval queue</h2>
                      <p className={styles.panelDesc}>Highest-signal review work.</p>
                    </div>
                  </div>
                  <div className={styles.queueList}>
                    {queue.map((item) => (
                      <div className={styles.queueItem} key={item.title}>
                        <div>
                          <div className={styles.queueTitle}>{item.title}</div>
                          <div className={styles.queueMeta}>{item.meta}</div>
                        </div>
                        <div className={styles.queueScore}>{item.score}</div>
                      </div>
                    ))}
                  </div>
                </section>

                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Inspector</h2>
                      <p className={styles.panelDesc}>Pinned run context.</p>
                    </div>
                  </div>
                  <div className={styles.inspectorBody}>
                    <dl className={styles.inspectorBlock}>
                      <div className={styles.kv}>
                        <dt>run_id</dt>
                        <dd className={styles.runId}>r_8df2a91c</dd>
                      </div>
                      <div className={styles.kv}>
                        <dt>model</dt>
                        <dd>gpt-4.1-mini</dd>
                      </div>
                      <div className={styles.kv}>
                        <dt>workspace</dt>
                        <dd>acme-prod</dd>
                      </div>
                      <div className={styles.kv}>
                        <dt>sample rate</dt>
                        <dd>100%</dd>
                      </div>
                    </dl>
                    <pre className={styles.code}>{`{
  "intent": "refund_policy",
  "route": "support/escalation",
  "judge": "policy_groundedness"
}`}</pre>
                  </div>
                  <div className={styles.sparkline} aria-label="Traffic sparkline">
                    {spark.map((height, index) => (
                      <span
                        key={`${height}-${index}`}
                        className={`${styles.sparkBar} ${
                          height > 70 ? styles.sparkBarHot : ""
                        }`}
                        style={{ height: `${height}%` }}
                      />
                    ))}
                  </div>
                </section>
              </aside>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function PreviewSidebar() {
  return (
    <aside className={styles.sidebar}>
      <Link className={styles.brand} href="/">
        <span className={styles.brandMark}>t</span>
        <span className={styles.brandText}>
          <span className={styles.brandName}>langprobe</span>
          <span className={styles.brandSub}>agent debugger</span>
        </span>
      </Link>

      <section className={styles.projectPanel} aria-label="Active project">
        <div className={styles.projectLabel}>Project</div>
        <div className={styles.projectName}>
          acme-prod
          <ChevronDown size={14} strokeWidth={1.7} />
        </div>
        <div className={styles.healthRow}>
          <div className={styles.meter}>
            <span className={styles.meterFill} />
          </div>
          <span>healthy</span>
        </div>
      </section>

      <nav className={styles.nav} aria-label="Preview navigation">
        {navGroups.map((group) => (
          <div className={styles.navGroup} key={group.label}>
            <span className={styles.sectionLabel}>{group.label}</span>
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <a
                  href="#"
                  className={`${styles.navItem} ${
                    item.active ? styles.navItemActive : ""
                  }`}
                  key={item.label}
                >
                  <Icon size={16} strokeWidth={1.6} />
                  <span>{item.label}</span>
                </a>
              );
            })}
          </div>
        ))}
      </nav>

      <footer className={styles.sidebarFooter}>
        <div className={styles.usage}>
          <span>ingest</span>
          <span>72% quota</span>
        </div>
        <div className={styles.userRow}>
          <span className={styles.avatar}>m</span>
          <span className={styles.userMeta}>
            <span className={styles.userEmail}>mia@example.com</span>
            <span className={styles.userRole}>workspace owner</span>
          </span>
          <Settings size={15} strokeWidth={1.7} />
        </div>
      </footer>
    </aside>
  );
}

function Topbar() {
  return (
    <header className={styles.topbar}>
      <div className={styles.crumbs}>
        <span>acme-prod</span>
        <span>/</span>
        <span className={styles.crumbLast}>overview</span>
      </div>
      <div className={styles.topbarSpacer} />
      <div className={styles.search} role="search">
        <Search size={15} strokeWidth={1.6} />
        <span className={styles.searchText}>Search runs, prompts, evals</span>
        <span className="kbd">⌘K</span>
      </div>
    </header>
  );
}

function kindClass(kind: string) {
  const k = (kind || "").toLowerCase();
  if (k === "llm") return `${styles.badge} ${styles.badgeLlm}`;
  if (k === "tool") return `${styles.badge} ${styles.badgeTool}`;
  if (k === "retriever" || k === "retr" || k === "reranker")
    return `${styles.badge} ${styles.badgeRetr}`;
  return `${styles.badge} ${styles.badgeChain}`;
}
