"use client";

import { Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Project settings editor.
 *
 * PII redaction defaults ON and we make the operator type a confirmation
 * before turning it off — the locked plan flag is "PII redactor cannot be
 * silently disabled". Sampling and RCA cost ceiling shape the data plane,
 * so we surface them prominently rather than burying them in a JSON blob.
 *
 * Form structure: three labelled sections (Identity, Ingest, Evals) so a
 * dense settings card reads as logical groups instead of a wall of fields.
 * Sections share visual treatment with every other settings surface across
 * the app via .form-section in globals.css.
 */

type RcaMode = "off" | "errors_only" | "errors_and_poor" | "all";

export interface ProjectSettings {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  sample_rate: number;
  pii_redaction: boolean;
  eval_default_judge: string | null;
  eval_cost_ceiling_usd_per_day: string | null;
  rca_mode: RcaMode;
}

const RCA_OPTIONS: { value: RcaMode; label: string; hint: string }[] = [
  { value: "off", label: "Off", hint: "no automated RCA" },
  { value: "errors_only", label: "Errors only", hint: "default" },
  { value: "errors_and_poor", label: "Errors + poor scores", hint: "broader" },
  { value: "all", label: "All runs", hint: "expensive, set ceiling" },
];

export function ProjectSettingsForm({ project }: { project: ProjectSettings }) {
  const router = useRouter();
  const [name, setName] = useState(project.name);
  const [sampleRate, setSampleRate] = useState(String(project.sample_rate));
  const [piiRedaction, setPiiRedaction] = useState(project.pii_redaction);
  const [piiConfirm, setPiiConfirm] = useState("");
  const [rcaMode, setRcaMode] = useState<RcaMode>(project.rca_mode);
  const [costCeiling, setCostCeiling] = useState(
    project.eval_cost_ceiling_usd_per_day ?? "",
  );
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const dirty =
    name !== project.name ||
    Number(sampleRate) !== project.sample_rate ||
    piiRedaction !== project.pii_redaction ||
    rcaMode !== project.rca_mode ||
    (costCeiling || null) !==
      (project.eval_cost_ceiling_usd_per_day || null);

  const piiTurningOff = project.pii_redaction && !piiRedaction;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setOkMsg(null);

    const sr = Number(sampleRate);
    if (!Number.isFinite(sr) || sr < 0 || sr > 1) {
      setError("Sample rate must be between 0 and 1.");
      return;
    }
    if (piiTurningOff && piiConfirm !== "DISABLE") {
      setError(
        'Type DISABLE in the confirmation box to turn PII redaction off.',
      );
      return;
    }

    const patch: Record<string, unknown> = {};
    if (name !== project.name) patch.name = name;
    if (sr !== project.sample_rate) patch.sample_rate = sr;
    if (piiRedaction !== project.pii_redaction)
      patch.pii_redaction = piiRedaction;
    if (rcaMode !== project.rca_mode) patch.rca_mode = rcaMode;
    const cc = costCeiling.trim();
    if ((cc || null) !== (project.eval_cost_ceiling_usd_per_day || null)) {
      patch.eval_cost_ceiling_usd_per_day = cc === "" ? null : cc;
    }
    if (Object.keys(patch).length === 0) return;

    startTransition(async () => {
      const res = await fetch(`/api/projects/${project.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `Save failed (${res.status})`);
        return;
      }
      setOkMsg("Saved.");
      setPiiConfirm("");
      router.refresh();
    });
  }

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: 24 }}>
      {/* Identity */}
      <section className="form-section">
        <header className="form-section-head">
          <h3 className="form-section-title">Identity</h3>
          <p className="form-section-desc">
            How this project appears in the project switcher and on shared
            URLs. The slug is fixed once the project is created.
          </p>
        </header>
        <div className="field-row">
          <label className="field" style={{ maxWidth: 360 }}>
            <span className="field-label">Display name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={pending}
            />
            <span className="field-hint">Shown in the project switcher.</span>
          </label>
          <label className="field" style={{ maxWidth: 220 }}>
            <span className="field-label">Slug</span>
            <input
              type="text"
              value={project.slug}
              className="mono"
              readOnly
              disabled
            />
            <span className="field-hint">Used in URLs. Immutable.</span>
          </label>
        </div>
      </section>

      {/* Ingest */}
      <section className="form-section">
        <header className="form-section-head">
          <h3 className="form-section-title">Ingest</h3>
          <p className="form-section-desc">
            What fraction of traffic to keep and what to redact before it
            lands in your data plane.
          </p>
        </header>

        <label className="field" style={{ maxWidth: 220 }}>
          <span className="field-label">Sample rate</span>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={sampleRate}
            onChange={(e) => setSampleRate(e.target.value)}
            className="mono"
            disabled={pending}
          />
          <span className="field-hint">
            Fraction of runs to ingest (0.0 to 1.0). 1.0 keeps everything.
          </span>
        </label>

        <div className="field">
          <span className="field-label">PII redaction</span>
          <span className="field-hint">
            Strips emails, phone numbers, credit cards, and other PII from
            inputs and outputs at ingest. Cannot be silently disabled.
          </span>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
              marginTop: 6,
            }}
          >
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 10,
                cursor: pending ? "not-allowed" : "pointer",
                fontSize: 13,
                color: "var(--text)",
              }}
            >
              <input
                type="checkbox"
                checked={piiRedaction}
                onChange={(e) => setPiiRedaction(e.target.checked)}
                disabled={pending}
                style={{ width: "auto", margin: 0 }}
              />
              <span>{piiRedaction ? "On" : "Off"}</span>
              {project.pii_redaction ? (
                <span className="badge badge-success">on in production</span>
              ) : (
                <span className="badge badge-warn">off in production</span>
              )}
            </label>

            {piiTurningOff ? (
              <PiiConfirmInline
                value={piiConfirm}
                onChange={setPiiConfirm}
                disabled={pending}
              />
            ) : null}
          </div>
        </div>
      </section>

      {/* Evals + RCA */}
      <section className="form-section">
        <header className="form-section-head">
          <h3 className="form-section-title">Evals &amp; RCA</h3>
          <p className="form-section-desc">
            When to launch automated root-cause analysis, and how much it&apos;s
            allowed to spend per day across this project.
          </p>
        </header>

        <label className="field" style={{ maxWidth: 360 }}>
          <span className="field-label">RCA mode</span>
          <select
            value={rcaMode}
            onChange={(e) => setRcaMode(e.target.value as RcaMode)}
            disabled={pending}
          >
            {RCA_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label} ({o.hint})
              </option>
            ))}
          </select>
        </label>

        <label className="field" style={{ maxWidth: 280 }}>
          <span className="field-label">Eval cost ceiling</span>
          <div className="input-shell">
            <span
              className="mono"
              style={{ color: "var(--text-3)", fontSize: 13 }}
            >
              $
            </span>
            <input
              type="text"
              inputMode="decimal"
              placeholder="50.00"
              value={costCeiling}
              onChange={(e) => setCostCeiling(e.target.value)}
              className="mono"
              disabled={pending}
            />
            <span
              style={{
                color: "var(--text-3)",
                fontSize: 12,
                whiteSpace: "nowrap",
              }}
            >
              / day
            </span>
          </div>
          <span className="field-hint">
            Max USD per day for automated evals and RCA on this project.
            Leave blank for no ceiling.
          </span>
        </label>
      </section>

      {error ? (
        <p className="field-error" role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      ) : null}

      <div className="form-actions">
        {okMsg ? (
          <span
            className="form-actions-note"
            style={{ color: "var(--success)" }}
          >
            {okMsg}
          </span>
        ) : (
          <span className="form-actions-note">
            {dirty ? "Unsaved changes." : "All changes saved."}
          </span>
        )}
        <button
          type="submit"
          className="btn btn-primary"
          disabled={pending || !dirty}
        >
          <Save size={14} strokeWidth={1.75} />
          {pending ? "Saving…" : "Save changes"}
        </button>
      </div>
    </form>
  );
}

function PiiConfirmInline({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <div
      role="alertdialog"
      style={{
        padding: 12,
        background: "var(--warn-soft)",
        border: "1px solid var(--warn)",
        borderRadius: "var(--r-4)",
        color: "var(--warn)",
        fontSize: 12,
        lineHeight: 1.55,
        display: "grid",
        gap: 8,
      }}
    >
      <div>
        Type <span className="mono">DISABLE</span> to confirm. New traces
        will store inputs and outputs verbatim. Existing traces are
        unchanged.
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="DISABLE"
        className="mono"
        disabled={disabled}
        style={{ width: 220 }}
        aria-label="Type DISABLE to confirm"
      />
    </div>
  );
}
