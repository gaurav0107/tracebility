"use client";

import { Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { ProviderModelPicker } from "@/components/ModelPicker";

/**
 * Client controls for /judges:
 *   - NewJudgeButton: modal that POSTs /api/luna-judges
 *   - DeleteJudgeButton: per-row soft-delete
 *
 * The judge slug is the operator-facing handle that gets referenced
 * at eval/poll-run time as `luna:<slug>`. Server validates the slug
 * regex; we surface server errors inline rather than duplicating.
 */

export interface LunaJudgeRow {
  id: string;
  project_id: string;
  slug: string;
  name: string;
  description: string | null;
  rubric_prompt: string;
  output_format: "score-rationale" | "json-object";
  provider:
    | "anthropic"
    | "openai"
    | "gemini"
    | "mistral"
    | "deepseek"
    | "groq"
    | "stub";
  model: string;
  temperature: number | null;
  max_tokens: number;
  created_at: string;
  updated_at: string;
}

const FORMAT_OPTIONS = [
  {
    value: "score-rationale",
    label: "score-rationale",
    hint: 'expects "score: 0.X" + "rationale: ..." in the response',
  },
  {
    value: "json-object",
    label: "json-object",
    hint: 'expects {"score": 0.X, "rationale": "..."} JSON',
  },
] as const;

const DEFAULT_RUBRIC = `You are evaluating an LLM response.
Inputs:
  - input: {{ input }}
  - expected: {{ expected }}
  - output: {{ output }}

Score the response from 0.0 to 1.0 on correctness vs. expected.
Respond exactly:
score: <number>
rationale: <one sentence>
`;

export function NewJudgeButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rubric, setRubric] = useState(DEFAULT_RUBRIC);
  const [provider, setProvider] = useState<
    Exclude<LunaJudgeRow["provider"], "stub">
  >("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [temperature, setTemperature] = useState("0.0");
  const [maxTokens, setMaxTokens] = useState("512");
  const [outputFormat, setOutputFormat] =
    useState<LunaJudgeRow["output_format"]>("score-rationale");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setSlug("");
    setName("");
    setDescription("");
    setRubric(DEFAULT_RUBRIC);
    setProvider("anthropic");
    setModel("claude-sonnet-4-6");
    setTemperature("0.0");
    setMaxTokens("512");
    setOutputFormat("score-rationale");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
      setError("slug must match ^[a-z0-9][a-z0-9_-]*$");
      return;
    }
    if (!name.trim()) {
      setError("name required");
      return;
    }
    if (!rubric.trim()) {
      setError("rubric required");
      return;
    }
    const t = Number(temperature);
    if (!Number.isFinite(t) || t < 0 || t > 2) {
      setError("temperature must be 0.0..2.0");
      return;
    }
    const m = Number(maxTokens);
    if (!Number.isFinite(m) || m < 1 || m > 4096) {
      setError("max_tokens must be 1..4096");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/luna-judges", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          slug,
          name: name.trim(),
          description: description.trim() || null,
          rubric_prompt: rubric,
          output_format: outputFormat,
          provider,
          model: model.trim(),
          temperature: t,
          max_tokens: Math.round(m),
        }),
      });
      if (!res.ok && res.status !== 201) {
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
        <Plus size={14} /> New judge
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
        padding: "5vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(720px, 100%)", display: "grid", gap: 12, maxHeight: "90vh", overflow: "auto" }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <h2 style={{ margin: 0 }}>New Luna judge</h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Slug" hint="lowercase, used as luna:<slug> at eval time">
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="strict-correctness"
            />
          </Field>
          <Field label="Name">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Strict correctness judge"
            />
          </Field>
        </div>
        <Field label="Description (optional)">
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="what this judge measures"
          />
        </Field>
        <Field
          label="Rubric prompt"
          hint="use {{ input }}, {{ expected }}, {{ output }} as variables"
        >
          <textarea
            value={rubric}
            onChange={(e) => setRubric(e.target.value)}
            rows={10}
            style={{ fontFamily: "var(--f-mono)", fontSize: 12 }}
          />
        </Field>
        <ProviderModelPicker
          provider={provider}
          model={model}
          onChange={({ provider: p, model: m }) => {
            setProvider(p);
            setModel(m);
          }}
        />
        <p className="field-hint" style={{ margin: 0 }}>
          tip: pick &lsquo;Custom&hellip;&rsquo; and type{" "}
          <code>stub/echo</code> for the deterministic test path that
          bypasses LiteLLM.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 2fr", gap: 12 }}>
          <Field label="Temperature">
            <input
              value={temperature}
              inputMode="decimal"
              onChange={(e) => setTemperature(e.target.value)}
            />
          </Field>
          <Field label="Max tokens">
            <input
              value={maxTokens}
              inputMode="numeric"
              onChange={(e) => setMaxTokens(e.target.value)}
            />
          </Field>
          <Field label="Output format">
            <select
              value={outputFormat}
              onChange={(e) =>
                setOutputFormat(e.target.value as LunaJudgeRow["output_format"])
              }
            >
              {FORMAT_OPTIONS.map((f) => (
                <option key={f.value} value={f.value} title={f.hint}>
                  {f.label}
                </option>
              ))}
            </select>
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
            {pending ? "creating…" : "Create judge"}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function DeleteJudgeButton({
  judgeId,
  slug,
}: {
  judgeId: string;
  slug: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    const ok = window.confirm(
      `Delete judge "${slug}"? Existing eval_score rows tagged luna:${slug} are kept for audit.`,
    );
    if (!ok) return;
    startTransition(async () => {
      const res = await fetch(`/api/luna-judges/${judgeId}`, { method: "DELETE" });
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
      title={error ?? "delete judge"}
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
