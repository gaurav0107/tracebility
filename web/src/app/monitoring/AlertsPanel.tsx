import {
  type AlertEventRow,
  type AlertRuleRow,
  DeleteAlertRuleButton,
  ToggleAlertEnabledButton,
} from "@/components/AlertsClient";
import type { Project } from "@/lib/projects";

/**
 * Alerts tab of /monitoring — rules over the same ClickHouse `run`
 * rollups the Dashboards tab charts. Server component; the page
 * fetches rules/events (it needs the rules anyway for the tab badge)
 * and passes them down. Client controls (new / snooze / delete) live
 * in `AlertsClient.tsx`.
 */

export function AlertsPanel({
  rules,
  events,
  rulesError,
  eventsError,
  project,
}: {
  rules: AlertRuleRow[];
  events: AlertEventRow[];
  rulesError: string | null;
  eventsError: string | null;
  project: Project;
}) {
  const activeRules = rules.filter((r) => r.enabled).length;
  const openIncidents = rules.filter((r) => r.open_incident_id).length;
  const eventsLast24h = events.filter((e) => isWithin24h(e.occurred_at)).length;
  const longestOpenSeconds = computeLongestOpen(rules, events);
  // A failed rules fetch must not render as "0 open incidents".
  const kpisUnavailable = Boolean(rulesError) && rules.length === 0;

  if (kpisUnavailable) {
    return (
      <>
        <section
          className="card"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
            gap: 0,
            overflow: "hidden",
          }}
        >
          {["Active rules", "Open incidents", "Events 24h", "Longest open"].map(
            (label, i) => (
              <KpiCell key={label} label={label} value="—" last={i === 3} />
            ),
          )}
        </section>
        <RulesCard rows={rules} reason={rulesError} project={project} />
        <EventsCard rows={events} reason={eventsError} />
      </>
    );
  }

  return (
    <>
      <section
        className="card"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: 0,
          overflow: "hidden",
        }}
      >
        <KpiCell label="Active rules" value={String(activeRules)} />
        <KpiCell
          label="Open incidents"
          value={String(openIncidents)}
          color={openIncidents > 0 ? "var(--danger)" : undefined}
        />
        <KpiCell label="Events 24h" value={String(eventsLast24h)} />
        <KpiCell
          label="Longest open"
          value={
            longestOpenSeconds === null ? "—" : fmtDuration(longestOpenSeconds)
          }
          color={
            longestOpenSeconds && longestOpenSeconds > 3600
              ? "var(--warn)"
              : undefined
          }
          last
        />
      </section>
      <RulesCard rows={rules} reason={rulesError} project={project} />
      <EventsCard rows={events} reason={eventsError} />
    </>
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

function RulesCard({
  rows,
  reason,
  project,
}: {
  rows: AlertRuleRow[];
  reason: string | null;
  project: Project;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>Rules</h2>
          <span className="card-sub">
            scoped to <span className="mono">{project.slug}</span>
          </span>
        </div>
      </div>
      {rows.length === 0 ? (
        <EmptyRulesState reason={reason} project={project} />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Name</th>
                <th>Condition</th>
                <th style={{ textAlign: "right" }}>Window</th>
                <th style={{ textAlign: "right" }}>Last value</th>
                <th>Routes</th>
                <th style={{ textAlign: "right" }}>Last evaluated</th>
                <th style={{ textAlign: "right" }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <RuleStatusBadge rule={r} />
                  </td>
                  <td>{r.name}</td>
                  <td>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {r.metric} {r.comparator}{" "}
                      {fmtMetricValue(r.metric, r.threshold)}
                    </span>
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {fmtDuration(r.window_seconds)}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {r.last_value === null ? (
                      <span style={{ color: "var(--text-3)" }}>—</span>
                    ) : (
                      <span className="mono">
                        {fmtMetricValue(r.metric, r.last_value)}
                      </span>
                    )}
                  </td>
                  <td>
                    <RoutesCell routes={r.routes} />
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {r.last_evaluated_at
                      ? fmtDateTime(r.last_evaluated_at)
                      : "—"}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div
                      style={{
                        display: "inline-flex",
                        gap: 4,
                        justifyContent: "flex-end",
                      }}
                    >
                      <ToggleAlertEnabledButton rule={r} />
                      <DeleteAlertRuleButton ruleId={r.id} name={r.name} />
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

function RuleStatusBadge({ rule }: { rule: AlertRuleRow }) {
  if (!rule.enabled) {
    return <span className="badge badge-neutral">snoozed</span>;
  }
  if (rule.open_incident_id) {
    return <span className="badge badge-danger">firing</span>;
  }
  if (rule.last_evaluated_at) {
    return <span className="badge badge-success">ok</span>;
  }
  return <span className="badge badge-neutral">pending</span>;
}

function RoutesCell({ routes }: { routes: AlertRuleRow["routes"] }) {
  if (!routes || routes.length === 0) {
    return <span style={{ color: "var(--text-3)", fontSize: 12 }}>none</span>;
  }
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {routes.map((r, i) => (
        <span
          key={`${r.kind}:${r.target}:${i}`}
          className="badge badge-neutral"
          style={{ fontSize: 11 }}
          title={`${r.kind}: ${r.target}`}
        >
          {r.kind}
        </span>
      ))}
    </div>
  );
}

function EventsCard({
  rows,
  reason,
}: {
  rows: AlertEventRow[];
  reason: string | null;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <h2>Recent events</h2>
        <span className="card-sub">last 200, newest first</span>
      </div>
      {rows.length === 0 ? (
        <div style={{ padding: 24 }}>
          <p style={{ color: "var(--text-2)", margin: 0, lineHeight: 1.55 }}>
            No alert events yet.
            {reason ? (
              <span
                className="mono"
                style={{ marginLeft: 8, fontSize: 11, color: "var(--text-3)" }}
              >
                ({reason})
              </span>
            ) : null}
          </p>
        </div>
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Rule</th>
                <th style={{ textAlign: "right" }}>Value</th>
                <th style={{ textAlign: "right" }}>Threshold</th>
                <th>Incident</th>
                <th style={{ textAlign: "right" }}>When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td>
                    <EventKindBadge kind={e.kind} />
                  </td>
                  <td>
                    {e.rule_name ?? (
                      <span className="mono" style={{ color: "var(--text-3)" }}>
                        {e.rule_id.slice(0, 8)}
                      </span>
                    )}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {fmtNumber(e.value)}
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {fmtNumber(e.threshold)}
                  </td>
                  <td>
                    <span
                      className="mono"
                      style={{ fontSize: 11, color: "var(--text-3)" }}
                    >
                      {e.incident_id.slice(0, 8)}
                    </span>
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {fmtDateTime(e.occurred_at)}
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

function EventKindBadge({ kind }: { kind: string }) {
  if (kind === "fired") {
    return <span className="badge badge-danger">fired</span>;
  }
  if (kind === "resolved") {
    return <span className="badge badge-success">resolved</span>;
  }
  return <span className="badge badge-neutral">{kind}</span>;
}

function EmptyRulesState({
  reason,
  project,
}: {
  reason: string | null;
  project: Project;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>
        No alert rules yet in <span className="mono">{project.slug}</span>.
      </h3>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          maxWidth: 640,
        }}
      >
        Click <strong>New alert</strong> to define a threshold over the same
        ClickHouse rollups the Dashboards tab charts. Routes are stored now;
        Slack and PagerDuty delivery slot in next iteration without changing
        the rule shape.
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
// Helpers
// ---------------------------------------------------------------------------

function fmtMetricValue(metric: string, value: number): string {
  if (metric === "error_rate") return `${(value * 100).toFixed(2)}%`;
  if (metric === "latency_p95_ms") return `${value.toFixed(0)}ms`;
  if (metric === "runs_per_min") return `${value.toFixed(2)}/min`;
  if (metric === "cost_usd") return `$${value.toFixed(4)}`;
  return fmtNumber(value);
}

function fmtNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
  } catch {
    return iso;
  }
}

function isWithin24h(iso: string): boolean {
  try {
    return Date.now() - new Date(iso).getTime() <= 24 * 3600 * 1000;
  } catch {
    return false;
  }
}

function computeLongestOpen(
  rules: AlertRuleRow[],
  events: AlertEventRow[],
): number | null {
  // Despite the name, `rule.open_incident_id` stores the id of the FIRED
  // EVENT that opened the incident (see services/api routers/alerts.py),
  // so keying by e.id is correct. Known limit: events are capped at the
  // newest 200 — an incident whose fired event has scrolled past that cap
  // drops out of this KPI.
  const byEventId = new Map(events.map((e) => [e.id, e]));
  let longest: number | null = null;
  const now = Date.now();
  for (const r of rules) {
    if (!r.open_incident_id) continue;
    const ev = byEventId.get(r.open_incident_id);
    if (!ev) continue;
    const opened = new Date(ev.occurred_at).getTime();
    if (Number.isNaN(opened)) continue;
    const seconds = Math.max(0, Math.round((now - opened) / 1000));
    if (longest === null || seconds > longest) longest = seconds;
  }
  return longest;
}
