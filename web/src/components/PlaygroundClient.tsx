"use client";

import { ExternalLink, Loader2, Play, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";

import { ModelPicker } from "@/components/ModelPicker";
import { MessageEditor } from "@/components/playground/MessageEditor";
import { SavePromptForm } from "@/components/playground/SavePromptForm";

/**
 * Interactive Playground canvas.
 *
 * The page server-fetches the prompt catalog and recent sessions; this
 * component owns the live composer: pick a prompt + version (or paste
 * raw), edit variables detected from {{ var }} substitution, pick a
 * model, click Run, see the output + token counts + latency, deep-link
 * to the trace at /runs/{run_id}.
 *
 * Mode is "single" by default; flip to "compare" for a side-by-side run
 * of the same prompt against two models. Each Run is one POST -- we do
 * not stream tokens in v1 (the server is sync; streaming wires next
 * iteration without changing the storage shape).
 */

export interface Message {
  role: "system" | "human";
  content: string;
}

export interface PromptOption {
  id: string;
  slug: string;
  name: string;
  versions: {
    id: string;
    version: number;
    /** Structured form (Plan A+B). Always set on new versions. */
    template_messages: Message[];
    /** Legacy single-string field. Kept for back-compat reads from the
     *  api during the deprecation window. We don't display this. */
    template: string;
  }[];
}

export interface PlaygroundSessionOut {
  id: string;
  project_id: string;
  prompt_version_id: string | null;
  raw_template: string | null;
  rendered_prompt: string;
  variables: Record<string, unknown>;
  provider: string;
  model: string;
  temperature: number | null;
  max_tokens: number | null;
  status: "queued" | "running" | "done" | "failed";
  output_text: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  run_id: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

const VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;

function extractVariables(template: string): string[] {
  const out = new Set<string>();
  let match: RegExpExecArray | null;
  const re = new RegExp(VAR_RE);
  while ((match = re.exec(template)) !== null) {
    out.add(match[1]);
  }
  return [...out];
}

/**
 * Run the same variable-detection regex across a list of messages and
 * return the deduped union (preserving first-seen order). Used by the
 * composer to keep the Inputs panel in sync as the user edits System
 * and Human bodies.
 */
export function extractVariablesFromMessages(messages: Message[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of messages) {
    for (const v of extractVariables(m.content)) {
      if (!seen.has(v)) {
        seen.add(v);
        out.push(v);
      }
    }
  }
  return out;
}

export function PlaygroundComposer({
  projectId,
  prompts,
}: {
  projectId: string;
  prompts: PromptOption[];
}) {
  const [mode, setMode] = useState<"single" | "compare">("single");
  const [promptId, setPromptId] = useState<string>("");
  const [versionId, setVersionId] = useState<string>("");
  // Composer state. messages is the editable list; loadedVersionId is the
  // prompt_version we last loaded (null = unsaved free-form composition).
  const [messages, setMessages] = useState<Message[]>([
    { role: "system", content: "" },
    {
      role: "human",
      content: "Summarize the following text in one sentence:\n\n{{ text }}",
    },
  ]);
  const [loadedVersionId, setLoadedVersionId] = useState<string | null>(null);
  const [variables, setVariables] = useState<Record<string, string>>({
    text: "langprobe is a self-hosted LLM observability platform.",
  });
  const [model, setModel] = useState<string>("anthropic/claude-sonnet-4-6");
  const [modelB, setModelB] = useState<string>("openai/gpt-4o-mini");
  const [temperature, setTemperature] = useState<string>("0.7");
  const [maxTokens, setMaxTokens] = useState<string>("1024");
  const [result, setResult] = useState<PlaygroundSessionOut | null>(null);
  const [resultB, setResultB] = useState<PlaygroundSessionOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const selectedPrompt = useMemo(
    () => prompts.find((p) => p.id === promptId) ?? null,
    [prompts, promptId],
  );
  const selectedVersion = useMemo(
    () => selectedPrompt?.versions.find((v) => v.id === versionId) ?? null,
    [selectedPrompt, versionId],
  );

  // Loading a saved version replaces the editable messages and pins
  // loadedVersionId so Run posts prompt_version_id (not raw_messages).
  // Editing afterwards keeps loadedVersionId — Save then sends a new
  // version under the same prompt; the api short-circuits identical
  // bodies.
  useEffect(() => {
    if (selectedVersion === null) return;
    const next =
      selectedVersion.template_messages.length > 0
        ? selectedVersion.template_messages
        : [{ role: "human" as const, content: selectedVersion.template }];
    setMessages(next);
    setLoadedVersionId(selectedVersion.id);
  }, [selectedVersion]);

  const detectedVars = useMemo(
    () => extractVariablesFromMessages(messages),
    [messages],
  );

  const isComposerEmpty = !messages.some((m) => m.content.trim().length > 0);

  // Save flow. Two paths:
  //   1. loadedVersionId !== null → POST a new version under the same prompt.
  //      Plan B's api short-circuits identical messages and returns the
  //      existing row (HTTP 200), so re-saving an unchanged composer is
  //      a cheap no-op.
  //   2. loadedVersionId === null → show the inline name+slug form; on
  //      submit, create the prompt then post v1 and switch the composer
  //      to the loaded state so subsequent saves create v2/v3/...
  const [showSaveForm, setShowSaveForm] = useState(false);
  const [savingBusy, setSavingBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const canSave = !isComposerEmpty && !savingBusy && !pending;

  async function postNewVersion(
    targetPromptId: string,
  ): Promise<{ id: string } | null> {
    const resp = await fetch(`/api/prompts/${encodeURIComponent(targetPromptId)}/versions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ template_messages: messages }),
    });
    if (!resp.ok && resp.status !== 200 && resp.status !== 201) {
      let detail = `request failed (${resp.status})`;
      try {
        const data = await resp.json();
        if (data && typeof data === "object" && "detail" in data) {
          detail = String((data as { detail: unknown }).detail);
        }
      } catch {
        /* ignore */
      }
      setSaveError(detail);
      return null;
    }
    return (await resp.json()) as { id: string };
  }

  async function saveExisting() {
    if (loadedVersionId === null) return;
    const owner = prompts.find((p) =>
      p.versions.some((v) => v.id === loadedVersionId),
    );
    if (!owner) {
      setSaveError("loaded version not found in prompt catalog");
      return;
    }
    setSavingBusy(true);
    setSaveError(null);
    try {
      const created = await postNewVersion(owner.id);
      if (created) {
        setLoadedVersionId(created.id);
        router.refresh();
      }
    } finally {
      setSavingBusy(false);
    }
  }

  async function saveNew(form: { name: string; slug: string }) {
    setSavingBusy(true);
    setSaveError(null);
    try {
      const create = await fetch("/api/prompts", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          slug: form.slug,
          name: form.name,
        }),
      });
      if (!create.ok) {
        let detail = `prompt create failed (${create.status})`;
        try {
          const data = await create.json();
          if (data && typeof data === "object" && "detail" in data) {
            detail = String((data as { detail: unknown }).detail);
          }
        } catch {
          /* ignore */
        }
        setSaveError(detail);
        return;
      }
      const prompt = (await create.json()) as { id: string };
      const created = await postNewVersion(prompt.id);
      if (created) {
        setShowSaveForm(false);
        setPromptId(prompt.id);
        setVersionId(created.id);
        setLoadedVersionId(created.id);
        router.refresh();
      }
    } finally {
      setSavingBusy(false);
    }
  }

  function onClickSave() {
    setSaveError(null);
    if (loadedVersionId !== null) {
      void saveExisting();
    } else {
      setShowSaveForm(true);
    }
  }

  function setVariable(key: string, value: string) {
    setVariables((prev) => ({ ...prev, [key]: value }));
  }

  async function runOne(modelChoice: string): Promise<PlaygroundSessionOut | null> {
    const tempNum = temperature.trim() === "" ? null : Number(temperature);
    if (tempNum !== null && (!Number.isFinite(tempNum) || tempNum < 0 || tempNum > 2)) {
      setError("temperature must be a number in [0.0, 2.0]");
      return null;
    }
    const maxNum = maxTokens.trim() === "" ? null : Number(maxTokens);
    if (maxNum !== null && (!Number.isFinite(maxNum) || maxNum < 1 || maxNum > 8192)) {
      setError("max_tokens must be 1..8192");
      return null;
    }
    const body: Record<string, unknown> = {
      project_id: projectId,
      variables: Object.fromEntries(
        Object.entries(variables).map(([k, v]) => [k, v]),
      ),
      model: modelChoice,
      temperature: tempNum,
      max_tokens: maxNum,
    };
    if (loadedVersionId !== null) {
      body.prompt_version_id = loadedVersionId;
    } else {
      if (isComposerEmpty) {
        setError("prompt is empty");
        return null;
      }
      body.raw_messages = messages;
    }
    const res = await fetch("/api/playground/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok && res.status !== 201) {
      let data: unknown;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : `request failed (${res.status})`;
      setError(detail);
      return null;
    }
    return (await res.json()) as PlaygroundSessionOut;
  }

  function run() {
    setError(null);
    setResult(null);
    setResultB(null);
    startTransition(async () => {
      if (mode === "single") {
        const r = await runOne(model);
        if (r) setResult(r);
      } else {
        const [r1, r2] = await Promise.all([runOne(model), runOne(modelB)]);
        if (r1) setResult(r1);
        if (r2) setResultB(r2);
      }
      router.refresh();
    });
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <ModeToggle mode={mode} onChange={setMode} />
      <PromptSourceCard
        prompts={prompts}
        promptId={promptId}
        setPromptId={setPromptId}
        versionId={versionId}
        setVersionId={setVersionId}
        selectedPrompt={selectedPrompt}
        messages={messages}
        setMessages={setMessages}
        loadedVersionId={loadedVersionId}
        setLoadedVersionId={setLoadedVersionId}
        canSave={canSave}
        savingBusy={savingBusy}
        saveError={saveError}
        showSaveForm={showSaveForm}
        onClickSave={onClickSave}
        onCancelSave={() => setShowSaveForm(false)}
        onSubmitSave={saveNew}
      />
      <VariablesCard
        keys={detectedVars}
        values={variables}
        onChange={setVariable}
      />
      <ModelCard
        mode={mode}
        model={model}
        setModel={setModel}
        modelB={modelB}
        setModelB={setModelB}
        temperature={temperature}
        setTemperature={setTemperature}
        maxTokens={maxTokens}
        setMaxTokens={setMaxTokens}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 12,
        }}
      >
        {error ? (
          <span
            className="mono"
            style={{ color: "var(--danger)", fontSize: 12, alignSelf: "center" }}
          >
            {error}
          </span>
        ) : null}
        <button
          type="button"
          className="btn btn-primary btn-lg"
          onClick={run}
          disabled={pending || (loadedVersionId === null && isComposerEmpty)}
          style={{ fontSize: 14, fontWeight: 700, gap: 9 }}
        >
          {pending ? (
            <>
              <Loader2 size={14} className="spin" /> running…
            </>
          ) : (
            <>
              {mode === "compare" ? "run both" : "run"}{" "}
              <Play size={11} style={{ fill: "currentColor" }} />
            </>
          )}
        </button>
      </div>
      {result ? <OutputCard result={result} /> : null}
      {resultB ? <OutputCard result={resultB} /> : null}
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: "single" | "compare";
  onChange: (m: "single" | "compare") => void;
}) {
  const segStyle = (active: boolean): React.CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: 12.5,
    fontWeight: active ? 700 : 600,
    color: active ? "var(--text)" : "var(--text-3)",
    background: active ? "var(--surface)" : "transparent",
    boxShadow: active ? "0 2px 6px rgba(10,10,10,0.08)" : "none",
    borderRadius: "var(--r-pill)",
    padding: "6px 16px",
    border: 0,
    cursor: "pointer",
    fontFamily: "var(--f-sans)",
    letterSpacing: "-0.005em",
  });
  return (
    <div
      style={{
        display: "inline-flex",
        gap: 2,
        background: "var(--surface-3)",
        borderRadius: "var(--r-pill)",
        padding: 3,
      }}
    >
      <button
        type="button"
        onClick={() => onChange("single")}
        style={segStyle(mode === "single")}
      >
        single model
      </button>
      <button
        type="button"
        onClick={() => onChange("compare")}
        style={segStyle(mode === "compare")}
      >
        <Sparkles size={13} /> side-by-side
      </button>
    </div>
  );
}

function PromptSourceCard({
  prompts,
  promptId,
  setPromptId,
  versionId,
  setVersionId,
  selectedPrompt,
  messages,
  setMessages,
  loadedVersionId,
  setLoadedVersionId,
  canSave,
  savingBusy,
  saveError,
  showSaveForm,
  onClickSave,
  onCancelSave,
  onSubmitSave,
}: {
  prompts: PromptOption[];
  promptId: string;
  setPromptId: (s: string) => void;
  versionId: string;
  setVersionId: (s: string) => void;
  selectedPrompt: PromptOption | null;
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  loadedVersionId: string | null;
  setLoadedVersionId: (id: string | null) => void;
  canSave: boolean;
  savingBusy: boolean;
  saveError: string | null;
  showSaveForm: boolean;
  onClickSave: () => void;
  onCancelSave: () => void;
  onSubmitSave: (form: { name: string; slug: string }) => void;
}) {
  function clearLoaded() {
    setPromptId("");
    setVersionId("");
    setLoadedVersionId(null);
  }

  return (
    <section
      className="card card-pad-lg"
      style={{ display: "grid", gap: 12 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span className="card-title">prompt</span>
        <span style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Field label="Load">
            <select
              value={promptId}
              onChange={(e) => {
                const next = e.target.value;
                setPromptId(next);
                setVersionId("");
                if (next === "") {
                  // Cleared the picker. Keep current composer messages but
                  // detach from any loaded version so Run sends raw_messages.
                  setLoadedVersionId(null);
                }
              }}
              disabled={prompts.length === 0}
              title={
                prompts.length === 0
                  ? "no prompts in catalog yet"
                  : undefined
              }
            >
              <option value="">— pick a prompt —</option>
              {prompts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.slug} — {p.name}
                </option>
              ))}
            </select>
          </Field>
          {selectedPrompt ? (
            <Field label="Version">
              <select
                value={versionId}
                onChange={(e) => setVersionId(e.target.value)}
              >
                <option value="">— pick —</option>
                {selectedPrompt.versions
                  .slice()
                  .sort((a, b) => b.version - a.version)
                  .map((v) => (
                    <option key={v.id} value={v.id}>
                      v{v.version}
                    </option>
                  ))}
              </select>
            </Field>
          ) : null}
          {loadedVersionId !== null ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={clearLoaded}
              title="detach from loaded version (subsequent edits will run as raw_messages)"
              style={{ fontSize: 12 }}
            >
              Unload
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={onClickSave}
            disabled={!canSave}
            title={
              loadedVersionId !== null
                ? "save as a new version under the loaded prompt"
                : "save as a new prompt"
            }
            style={{ fontSize: 12 }}
          >
            {savingBusy ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
      {messages.map((msg, i) => (
        <MessageEditor
          key={i}
          message={msg}
          canDelete={messages.length > 1}
          onChange={(next) =>
            setMessages((prev) => prev.map((m, j) => (j === i ? next : m)))
          }
          onDelete={() => {
            if (messages.length === 1) return; // never empty
            setMessages((prev) => prev.filter((_, j) => j !== i));
          }}
          onMoveUp={
            i === 0
              ? undefined
              : () =>
                  setMessages((prev) => {
                    const copy = [...prev];
                    [copy[i - 1], copy[i]] = [copy[i], copy[i - 1]];
                    return copy;
                  })
          }
          onMoveDown={
            i === messages.length - 1
              ? undefined
              : () =>
                  setMessages((prev) => {
                    const copy = [...prev];
                    [copy[i + 1], copy[i]] = [copy[i], copy[i + 1]];
                    return copy;
                  })
          }
        />
      ))}
      {showSaveForm ? (
        <SavePromptForm
          busy={savingBusy}
          onCancel={onCancelSave}
          onSubmit={onSubmitSave}
        />
      ) : null}
      {saveError ? (
        <div
          role="alert"
          style={{
            padding: "6px 10px",
            fontSize: 12,
            background: "var(--danger-soft)",
            color: "var(--danger)",
            borderRadius: "var(--r-2)",
          }}
        >
          {saveError}
        </div>
      ) : null}
      <button
        type="button"
        onClick={() =>
          setMessages((prev) => [...prev, { role: "human", content: "" }])
        }
        style={{
          width: "fit-content",
          display: "inline-flex",
          alignItems: "center",
          fontSize: 12,
          fontWeight: 600,
          fontFamily: "var(--f-sans)",
          color: "var(--text-4)",
          background: "transparent",
          border: "1px dashed var(--border-muted)",
          borderRadius: "var(--r-pill)",
          padding: "6px 16px",
          cursor: "pointer",
        }}
      >
        + add message
      </button>
    </section>
  );
}

function VariablesCard({
  keys,
  values,
  onChange,
}: {
  keys: string[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <section className="card card-pad-lg">
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <span className="card-title">variables</span>
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-4)" }}
        >
          detected from {"{{ var }}"} substitutions
        </span>
      </div>
      {keys.length === 0 ? (
        <p style={{ color: "var(--text-3)", margin: 0, fontSize: 13 }}>
          No variables detected in the template.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {keys.map((k) => (
            <div
              key={k}
              style={{
                display: "grid",
                gridTemplateColumns: "90px 1fr",
                gap: 12,
                alignItems: "start",
              }}
            >
              <span
                className="mono"
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--link)",
                  background: "var(--accent-fill)",
                  borderRadius: "var(--r-pill)",
                  padding: "5px 12px",
                  textAlign: "center",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {k}
              </span>
              <textarea
                aria-label={`value for ${k}`}
                value={values[k] ?? ""}
                onChange={(e) => onChange(k, e.target.value)}
                rows={2}
                placeholder={`value for {{ ${k} }}`}
                style={{
                  fontFamily: "var(--f-mono)",
                  fontSize: 12,
                  lineHeight: 1.65,
                  color: "var(--text-2)",
                  background: "var(--surface-sidebar)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: "var(--r-3)",
                  padding: "11px 14px",
                }}
              />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ModelCard({
  mode,
  model,
  setModel,
  modelB,
  setModelB,
  temperature,
  setTemperature,
  maxTokens,
  setMaxTokens,
}: {
  mode: "single" | "compare";
  model: string;
  setModel: (s: string) => void;
  modelB: string;
  setModelB: (s: string) => void;
  temperature: string;
  setTemperature: (s: string) => void;
  maxTokens: string;
  setMaxTokens: (s: string) => void;
}) {
  return (
    <section className="card card-pad-lg">
      <span className="card-title">model</span>
      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            mode === "compare" ? "1.4fr 1.4fr 1fr 1fr" : "1.6fr 1fr 1fr",
          gap: 12,
          marginTop: 14,
        }}
      >
        <ModelPicker
          label={mode === "compare" ? "Model A" : "Model"}
          value={model}
          onChange={setModel}
        />
        {mode === "compare" ? (
          <ModelPicker label="Model B" value={modelB} onChange={setModelB} />
        ) : null}
        <Field label="Temperature" hint="0.0..2.0 (blank = provider default)">
          <input
            className="mono"
            value={temperature}
            inputMode="decimal"
            onChange={(e) => setTemperature(e.target.value)}
            placeholder="0.7"
          />
        </Field>
        <Field label="Max tokens">
          <input
            className="mono"
            value={maxTokens}
            inputMode="numeric"
            onChange={(e) => setMaxTokens(e.target.value)}
            placeholder="1024"
          />
        </Field>
      </div>
    </section>
  );
}

function OutputCard({ result }: { result: PlaygroundSessionOut }) {
  const tone =
    result.status === "failed"
      ? "danger"
      : result.status === "done"
        ? "success"
        : "neutral";
  const failed = result.status === "failed";
  return (
    <section className="card card-pad-lg">
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span className="card-title">output</span>
        <span
          className="mono"
          style={{ fontSize: 11.5, color: "var(--text-4)" }}
        >
          {result.model}
        </span>
        <StatusBadge status={result.status} tone={tone} />
        <span style={{ flex: 1 }} />
        {result.run_id ? (
          <Link
            href={`/runs/${result.run_id}`}
            className="mono"
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              color: "var(--link)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            open in /runs <ExternalLink size={12} />
          </Link>
        ) : null}
      </header>
      <div
        style={{
          background: "var(--surface-2)",
          border: "1px solid var(--border-soft)",
          borderRadius: "var(--r-4)",
          padding: "16px 18px",
          marginTop: 14,
          fontSize: 13.5,
          lineHeight: 1.65,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 360,
          overflow: "auto",
          color: failed ? "var(--danger)" : "var(--text)",
          fontFamily: failed ? "var(--f-mono)" : "var(--f-sans)",
        }}
      >
        {failed ? result.error : result.output_text || "(empty)"}
        {failed && result.error?.includes("credential") ? (
          <span
            style={{
              display: "block",
              marginTop: 8,
              fontFamily: "var(--f-sans)",
              color: "var(--text-2)",
            }}
          >
            Add a provider key under{" "}
            <a href="/workspace/credentials">workspace → LLM credentials</a>,
            or run a <span className="mono">stub-*</span> model to smoke-test
            without one.
          </span>
        ) : null}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: 12,
          marginTop: 18,
        }}
      >
        <Stat label="Latency" value={result.latency_ms != null ? `${result.latency_ms} ms` : "—"} />
        <Stat label="Prompt tok" value={String(result.prompt_tokens ?? "—")} />
        <Stat label="Completion" value={String(result.completion_tokens ?? "—")} />
        <Stat label="Total" value={String(result.total_tokens ?? "—")} />
      </div>
    </section>
  );
}

function StatusBadge({
  status,
  tone,
}: {
  status: string;
  tone: "success" | "danger" | "neutral";
}) {
  const cls =
    tone === "success"
      ? "badge badge-success"
      : tone === "danger"
        ? "badge badge-danger"
        : "badge badge-warn";
  const dotCls =
    tone === "success"
      ? "dot dot-success"
      : tone === "danger"
        ? "dot dot-danger"
        : "dot dot-warn";
  return (
    <span className={cls}>
      <span className={dotCls} aria-hidden />
      {status}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          color: "var(--text-4)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      <span
        className="mono num"
        style={{ fontSize: 15, fontWeight: 600, color: "var(--text)" }}
      >
        {value}
      </span>
    </div>
  );
}

function Field({
  label,
  hint,
  mono,
  children,
}: {
  label: string;
  hint?: string;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span
        className={mono ? "mono" : undefined}
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          color: "var(--text-4)",
          textTransform: mono ? "none" : "uppercase",
          letterSpacing: mono ? 0 : "0.08em",
        }}
      >
        {label}
      </span>
      {children}
      {hint ? (
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-4)" }}
        >
          {hint}
        </span>
      ) : null}
    </label>
  );
}
