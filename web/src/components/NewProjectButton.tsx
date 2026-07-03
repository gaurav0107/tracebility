"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * "New project" button — modal that posts to /api/projects, then pins
 * the new project as active and reloads.
 *
 * Workspace picker is required: a project belongs to exactly one
 * workspace, and most operators have only one but the data model
 * supports many. We pre-select the first workspace and let them
 * change.
 *
 * Slug regex matches the server: ^[a-z0-9][a-z0-9_-]*$. We surface
 * the server's 409 (slug collision) inline rather than hiding it.
 *
 * `variant="primary"` for the workspace-page header, `"empty-state"`
 * for the "no project resolved" CTA inside any page that hits the
 * empty path. Both share the same modal body.
 */

export interface WorkspaceOption {
  id: string;
  slug: string;
  name: string;
}

export function NewProjectButton({
  workspaces,
  variant = "primary",
}: {
  workspaces: WorkspaceOption[];
  variant?: "primary" | "empty-state";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<string>(
    workspaces[0]?.id ?? "",
  );
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setOpen(false);
    setWorkspaceId(workspaces[0]?.id ?? "");
    setSlug("");
    setName("");
    setError(null);
  }

  function submit() {
    setError(null);
    if (!workspaceId) {
      setError("no workspace available — see Members to set one up");
      return;
    }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
      setError("slug must match ^[a-z0-9][a-z0-9_-]*$ (lowercase, hyphens ok)");
      return;
    }
    if (!name.trim()) {
      setError("name required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          slug,
          name: name.trim(),
          // Defaults match the server-side ProjectCreate; we don't
          // surface them in the modal because the workspace page
          // can edit them post-creation.
          sample_rate: 1.0,
          pii_redaction: true,
          rca_mode: "errors_only",
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
      let createdId: string | null = null;
      try {
        const json = (await res.json()) as { id?: string };
        createdId = json.id ?? null;
      } catch {
        createdId = null;
      }
      if (createdId) {
        // Pin the new project as active so the user lands inside it.
        try {
          await fetch("/api/active-project", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ project_id: createdId }),
          });
        } catch {
          // best-effort; the user can switch in the sidebar
        }
      }
      reset();
      router.refresh();
    });
  }

  const trigger =
    variant === "empty-state" ? (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
        disabled={workspaces.length === 0}
        title={
          workspaces.length === 0
            ? "no workspace available — invite yourself or run setup"
            : undefined
        }
      >
        <Plus size={14} /> Create your first project
      </button>
    ) : (
      <button
        type="button"
        className="btn btn-primary"
        onClick={() => setOpen(true)}
        disabled={workspaces.length === 0}
        title={
          workspaces.length === 0
            ? "no workspace available — invite yourself or run setup"
            : "create a new project in this workspace"
        }
      >
        <Plus size={14} /> New project
      </button>
    );

  if (!open) return trigger;

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
        padding: "8vh 16px",
        zIndex: 50,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) reset();
      }}
    >
      <div
        className="card-elevated card-pad-lg"
        style={{ width: "min(560px, 100%)", display: "grid", gap: 12 }}
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
            New project
          </h2>
          <button type="button" className="btn btn-ghost" onClick={reset}>
            cancel
          </button>
        </header>
        {workspaces.length > 1 ? (
          <Field label="Workspace">
            <select
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.slug} — {w.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field
          label="Slug"
          hint="lowercase, used in URLs and SDK config (e.g. 'prod', 'staging')"
        >
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="prod"
          />
        </Field>
        <Field label="Display name" hint="shown in the project switcher">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Production"
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
            {pending ? "creating…" : "Create project"}
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
