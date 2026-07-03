"use client";

import { GitBranch, Tag } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * Client controls for the prompt detail page: new version, assign alias.
 *
 * input_schema and model_params are optional JSON objects; aliases are a
 * comma-separated list. The server enforces uniqueness-per-prompt and
 * strips the same alias off any prior version atomically, so this form
 * does not need to do that bookkeeping itself.
 */

export function NewVersionButton({ promptId }: { promptId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [template, setTemplate] = useState("");
  const [inputSchema, setInputSchema] = useState("");
  const [modelParams, setModelParams] = useState("");
  const [aliases, setAliases] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setTemplate("");
    setInputSchema("");
    setModelParams("");
    setAliases("");
    setCommitMessage("");
    setError(null);
  }

  function parseJsonObject(
    raw: string,
    label: string,
  ): Record<string, unknown> | null | "ERR" {
    if (!raw.trim()) return null;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
      setError(`${label} must be a JSON object`);
      return "ERR";
    } catch {
      setError(`${label} is not valid JSON`);
      return "ERR";
    }
  }

  function submit() {
    setError(null);
    if (!template.trim()) {
      setError("template is required");
      return;
    }
    const schema = parseJsonObject(inputSchema, "input schema");
    if (schema === "ERR") return;
    const params = parseJsonObject(modelParams, "model params");
    if (params === "ERR") return;
    const aliasList = aliases
      .split(",")
      .map((a) => a.trim())
      .filter(Boolean);

    startTransition(async () => {
      const res = await fetch(`/api/prompts/${promptId}/versions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          template,
          input_schema: schema,
          model_params: params,
          aliases: aliasList,
          commit_message: commitMessage.trim() || null,
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
        <GitBranch size={14} /> New version
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
        padding: "6vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(720px, 100%)", display: "grid", gap: 12 }}
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
            New prompt version
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <Field
          label="Template"
          hint="Jinja2-style; refers to vars in input schema"
        >
          <textarea
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            rows={10}
            placeholder={"You are a helpful assistant.\nUser: {{ user_message }}"}
            spellCheck={false}
            autoFocus
          />
        </Field>
        <Field label="Input schema (JSON object, optional)">
          <textarea
            value={inputSchema}
            onChange={(e) => setInputSchema(e.target.value)}
            rows={3}
            placeholder='{"type":"object","properties":{"user_message":{"type":"string"}}}'
            spellCheck={false}
          />
        </Field>
        <Field label="Model params (JSON object, optional)">
          <textarea
            value={modelParams}
            onChange={(e) => setModelParams(e.target.value)}
            rows={3}
            placeholder='{"model":"gpt-4o","temperature":0.2}'
            spellCheck={false}
          />
        </Field>
        <Field
          label="Aliases (optional)"
          hint="comma-separated; assigning here strips them off prior versions"
        >
          <input
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            placeholder="prod, staging"
            spellCheck={false}
          />
        </Field>
        <Field label="Commit message (optional)">
          <input
            value={commitMessage}
            onChange={(e) => setCommitMessage(e.target.value)}
            placeholder="why this revision exists"
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
            {pending ? "saving…" : "Save version"}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function AssignAliasButton({
  promptId,
  version,
}: {
  promptId: string;
  version: number;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [alias, setAlias] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setAlias("");
    setError(null);
  }

  function submit() {
    setError(null);
    const trimmed = alias.trim().toLowerCase();
    if (!trimmed) {
      setError("alias is required");
      return;
    }
    startTransition(async () => {
      const res = await fetch(`/api/prompts/${promptId}/aliases`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ alias: trimmed, version }),
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
        className="btn btn-ghost"
        onClick={() => setOpen(true)}
        title={`Assign alias to v${version}`}
      >
        <Tag size={12} /> alias
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
        style={{ width: "min(440px, 100%)", display: "grid", gap: 12 }}
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
            Assign alias{" "}
            <span className="mono" style={{ color: "var(--text-3)" }}>
              v{version}
            </span>
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--text-2)",
            lineHeight: 1.55,
          }}
        >
          Aliases are unique per prompt. Assigning <code>{alias || "@…"}</code>{" "}
          here removes it from any other version.
        </p>
        <Field label="Alias" hint="lowercase, e.g. prod / staging / champion">
          <input
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="prod"
            spellCheck={false}
            autoFocus
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
            {pending ? "assigning…" : "Assign alias"}
          </button>
        </footer>
      </div>
    </div>
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
