"use client";

import { Bell, BellOff, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Alerts list page: create, snooze, delete rules.
 *
 * Mirrors how ComparisonsClient is wired — server component on
 * `/alerts` parallel-fetches rules + events from FastAPI, this file
 * owns the form state and the optimistic-via-router-refresh mutations.
 * All writes go through the cookie-forwarding `/api/alerts` proxy
 * (`web/src/app/api/alerts/route.ts`) and `/api/alerts/[id]`.
 */

export interface AlertRoute {
  kind: string;
  target: string;
}

export interface AlertRuleRow {
  id: string;
  project_id: string;
  name: string;
  metric: string;
  comparator: string;
  threshold: number;
  window_seconds: number;
  routes: AlertRoute[];
  enabled: boolean;
  last_evaluated_at: string | null;
  last_value: number | null;
  open_incident_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AlertEventRow {
  id: string;
  rule_id: string;
  rule_name: string | null;
  project_id: string;
  kind: string;
  value: number;
  threshold: number;
  occurred_at: string;
  incident_id: string;
}

export const METRIC_OPTIONS: {
  value: string;
  label: string;
  hint: string;
}[] = [
  {
    value: "error_rate",
    label: "error_rate",
    hint: "share of error runs in window (0–1)",
  },
  {
    value: "latency_p95_ms",
    label: "latency_p95_ms",
    hint: "p95 run duration in milliseconds",
  },
  {
    value: "runs_per_min",
    label: "runs_per_min",
    hint: "average runs per minute over window",
  },
  {
    value: "cost_usd",
    label: "cost_usd",
    hint: "total spend over window (USD)",
  },
];

export const COMPARATOR_OPTIONS = [">", ">=", "<", "<="] as const;

export const ROUTE_KINDS: { value: string; label: string }[] = [
  { value: "slack", label: "slack channel" },
  { value: "pagerduty", label: "pagerduty service key" },
  { value: "webhook", label: "https webhook" },
  { value: "email", label: "email address" },
];

export function NewAlertRuleButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState<string>("error_rate");
  const [comparator, setComparator] = useState<string>(">");
  const [threshold, setThreshold] = useState("0.02");
  const [windowSeconds, setWindowSeconds] = useState("300");
  const [routeKind, setRouteKind] = useState<string>("slack");
  const [routeTarget, setRouteTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setName("");
    setMetric("error_rate");
    setComparator(">");
    setThreshold("0.02");
    setWindowSeconds("300");
    setRouteKind("slack");
    setRouteTarget("");
    setError(null);
  }

  function submit() {
    setError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("name required");
      return;
    }
    const thresholdNum = Number(threshold);
    if (!Number.isFinite(thresholdNum)) {
      setError("threshold must be a number");
      return;
    }
    const windowNum = Number(windowSeconds);
    if (!Number.isFinite(windowNum) || windowNum < 60 || windowNum > 86400) {
      setError("window must be between 60 and 86400 seconds");
      return;
    }
    const routes: AlertRoute[] = [];
    if (routeTarget.trim()) {
      routes.push({ kind: routeKind, target: routeTarget.trim() });
    }
    startTransition(async () => {
      const res = await fetch("/api/alerts", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: trimmedName,
          metric,
          comparator,
          threshold: thresholdNum,
          window_seconds: Math.round(windowNum),
          routes,
          enabled: true,
        }),
      });
      if (!res.ok) {
        let body: unknown;
        try {
          body = await res.json();
        } catch {
          body = null;
        }
        const detail =
          body && typeof body === "object" && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `request failed (${res.status})`;
        setError(detail);
        return;
      }
      reset();
      router.refresh();
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
      >
        <Plus size={14} /> New alert
      </button>
    );
  }

  const metricHint =
    METRIC_OPTIONS.find((m) => m.value === metric)?.hint ?? "";

  return (
    <div
      role="dialog"
      aria-modal
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--scrim)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "10vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(620px, 100%)", display: "grid", gap: 12 }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 18,
              fontWeight: 800,
              letterSpacing: "-0.02em",
            }}
          >
            New alert rule
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Name" hint="shows up in history + Slack message later">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. checkout error spike"
          />
        </Field>
        <div
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}
        >
          <Field label="Metric" hint={metricHint}>
            <select value={metric} onChange={(e) => setMetric(e.target.value)}>
              {METRIC_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Window seconds" hint="60–86400">
            <input
              type="text"
              value={windowSeconds}
              onChange={(e) => setWindowSeconds(e.target.value)}
              inputMode="numeric"
              placeholder="300"
            />
          </Field>
        </div>
        <div
          style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}
        >
          <Field label="Comparator">
            <select
              value={comparator}
              onChange={(e) => setComparator(e.target.value)}
            >
              {COMPARATOR_OPTIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Threshold" hint="value the metric is compared against">
            <input
              type="text"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              inputMode="decimal"
              placeholder="0.02"
            />
          </Field>
        </div>
        <div
          style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}
        >
          <Field label="Route kind" hint="stored in v1; delivery is next">
            <select
              value={routeKind}
              onChange={(e) => setRouteKind(e.target.value)}
            >
              {ROUTE_KINDS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Route target (optional)">
            <input
              type="text"
              value={routeTarget}
              onChange={(e) => setRouteTarget(e.target.value)}
              placeholder="#oncall  /  team-key  /  https://…"
            />
          </Field>
        </div>
        {error ? (
          <p className="field-error" style={{ margin: 0 }}>
            {error}
          </p>
        ) : null}
        <footer
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 4,
          }}
        >
          <button
            type="button"
            className="btn btn-primary"
            disabled={pending}
            onClick={submit}
          >
            {pending ? "creating…" : "Create rule"}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function ToggleAlertEnabledButton({ rule }: { rule: AlertRuleRow }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const next = !rule.enabled;
  const Icon = rule.enabled ? Bell : BellOff;
  const label = rule.enabled ? "snooze" : "enable";

  function submit() {
    setError(null);
    startTransition(async () => {
      const res = await fetch(`/api/alerts/${rule.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) {
        setError(`patch failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12, gap: 4 }}
      onClick={submit}
      disabled={pending}
      aria-label={label}
      title={error ?? label}
    >
      <Icon size={14} />
      {pending ? "…" : label}
    </button>
  );
}

export function DeleteAlertRuleButton({
  ruleId,
  name,
}: {
  ruleId: string;
  name: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    const ok = window.confirm(
      `Delete alert "${name}"? This also removes its event history.`,
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/alerts/${ruleId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        setError(`delete failed (${res.status})`);
        return;
      }
      router.refresh();
    });
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12, gap: 4, color: "var(--danger)" }}
      onClick={submit}
      disabled={pending}
      aria-label="delete"
      title={error ?? "delete rule"}
    >
      <Trash2 size={14} />
      {pending ? "…" : "delete"}
    </button>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}
