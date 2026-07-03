"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import type { Project } from "@/lib/projects";

/**
 * Project switcher — rendered as the design's sidebar pill: a full-radius
 * white chip with a green status dot, the project slug, and a ▾ caret. The
 * native <select> sits transparently on top so the control stays fully
 * accessible (keyboard + screen reader) while presenting as the pill.
 */
export function ProjectSwitcher({
  active,
  projects,
}: {
  active: Project | null;
  projects: Project[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  if (projects.length === 0) {
    return (
      <Link
        href="/workspace"
        title="create your first project"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--r-pill)",
          padding: "9px 14px",
          boxShadow: "var(--shadow-raised)",
          color: "var(--link)",
          fontSize: 13,
          fontWeight: 700,
          textDecoration: "none",
        }}
      >
        + create project
      </Link>
    );
  }

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-pill)",
        padding: "9px 14px",
        boxShadow: "var(--shadow-raised)",
        opacity: pending ? 0.6 : 1,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 7,
          height: 7,
          borderRadius: 9999,
          background: "var(--success)",
          flexShrink: 0,
        }}
      />
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 13,
          fontWeight: 700,
          color: "var(--text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {active?.slug ?? "select project"}
      </span>
      <span aria-hidden style={{ fontSize: 10, color: "var(--text-4)", flexShrink: 0 }}>
        ▾
      </span>
      <select
        aria-label="Active project"
        disabled={pending}
        value={active?.id ?? ""}
        onChange={(e) => {
          const projectId = e.target.value;
          startTransition(async () => {
            await fetch("/api/active-project", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ project_id: projectId }),
            });
            router.refresh();
          });
        }}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          opacity: 0,
          border: 0,
          padding: 0,
          margin: 0,
          cursor: "pointer",
          appearance: "none",
          WebkitAppearance: "none",
        }}
      >
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.slug}
          </option>
        ))}
      </select>
    </div>
  );
}
