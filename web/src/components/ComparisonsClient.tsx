"use client";

import { GitCompare } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the Comparisons list page: kick off a new
 * side-by-side run.
 *
 * A comparison pairs a dataset with two prompt versions and a judge.
 * The server inserts the queued row, dispatches a background runner
 * that scores every dataset item on both sides, and rolls per-side
 * averages back onto the postgres row. UI server-refreshes to pick
 * up status/avg as the runner progresses.
 */

export interface ComparisonRow {
  id: string;
  project_id: string;
  dataset_id: string;
  prompt_version_id_a: string;
  prompt_version_id_b: string;
  judge_kind: string;
  name: string | null;
  status: string;
  item_total: number;
  item_done_a: number;
  item_done_b: number;
  score_avg_a: number | null;
  score_avg_b: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatasetOption {
  id: string;
  slug: string;
  name: string;
  item_count: number;
}

export interface VersionOption {
  id: string;
  prompt_slug: string;
  version: number;
  aliases: string[];
}

export function NewComparisonButton({
  projectId,
  datasets,
  versions,
}: {
  projectId: string;
  datasets: DatasetOption[];
  versions: VersionOption[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [datasetId, setDatasetId] = useState(datasets[0]?.id ?? "");
  const [versionAId, setVersionAId] = useState(versions[0]?.id ?? "");
  const [versionBId, setVersionBId] = useState(
    versions[1]?.id ?? versions[0]?.id ?? "",
  );
  const [judgeKind, setJudgeKind] = useState<"echo" | "contains" | "exact">(
    "exact",
  );
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const enoughVersions = versions.length >= 2;
  const enoughDatasets = datasets.length >= 1;
  const ready = enoughVersions && enoughDatasets;

  function reset() {
    setOpen(false);
    setDatasetId(datasets[0]?.id ?? "");
    setVersionAId(versions[0]?.id ?? "");
    setVersionBId(versions[1]?.id ?? versions[0]?.id ?? "");
    setJudgeKind("exact");
    setName("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!datasetId) {
      setError("pick a dataset first");
      return;
    }
    if (!versionAId || !versionBId) {
      setError("pick two prompt versions");
      return;
    }
    if (versionAId === versionBId) {
      setError("variants must be different prompt versions");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/comparisons", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          dataset_id: datasetId,
          prompt_version_id_a: versionAId,
          prompt_version_id_b: versionBId,
          judge_kind: judgeKind,
          name: name.trim() || null,
        }),
      });
      if (!res.ok && res.status !== 202) {
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
    const hint = !enoughDatasets
      ? "Create a dataset first"
      : !enoughVersions
        ? "Need at least two prompt versions"
        : undefined;
    return (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
        disabled={!ready}
        title={hint}
      >
        <GitCompare size={14} /> New comparison
      </button>
    );
  }

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
            New comparison
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field label="Dataset" hint="every item is scored on both sides">
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
          >
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.slug} — {d.name} ({d.item_count})
              </option>
            ))}
          </select>
        </Field>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Variant A" hint="left side of the diff">
            <select
              value={versionAId}
              onChange={(e) => setVersionAId(e.target.value)}
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {versionLabel(v)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Variant B" hint="right side of the diff">
            <select
              value={versionBId}
              onChange={(e) => setVersionBId(e.target.value)}
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {versionLabel(v)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field
          label="Judge"
          hint="built-in v1; LLM-as-judge swaps in next iteration"
        >
          <select
            value={judgeKind}
            onChange={(e) =>
              setJudgeKind(e.target.value as "echo" | "contains" | "exact")
            }
          >
            <option value="exact">exact — pass if output == expected</option>
            <option value="contains">
              contains — pass if expected is substring of output
            </option>
            <option value="echo">echo — always 1.0 (smoke test)</option>
          </select>
        </Field>
        <Field label="Name (optional)">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. v14 vs @prod on triage regressions"
          />
        </Field>
        {error ? (
          <p
            className="mono"
            style={{ color: "var(--danger)", margin: 0, fontSize: 12 }}
          >
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
            {pending ? "queuing…" : "Run comparison"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function versionLabel(v: VersionOption): string {
  const aliasPart = v.aliases.length > 0 ? ` [@${v.aliases.join(", @")}]` : "";
  return `${v.prompt_slug} v${v.version}${aliasPart}`;
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
