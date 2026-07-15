"use client";

/**
 * Path-derived breadcrumbs. Every page used to fall back to
 * "{project} / overview" unless it hand-authored a crumbs prop; now
 * the default topbar crumb names the actual surface. Pages with richer
 * context (runs, monitoring) still pass explicit crumbs which win over
 * this component.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTION_LABELS: Record<string, { label: string; href: string }> = {
  "": { label: "overview", href: "/" },
  runs: { label: "tracing", href: "/runs" },
  threads: { label: "tracing", href: "/runs?view=threads" },
  monitoring: { label: "monitoring", href: "/monitoring" },
  alerts: { label: "alerts", href: "/monitoring?tab=alerts" },
  replay: { label: "replay", href: "/replay" },
  evals: { label: "evals", href: "/evals" },
  judges: { label: "judges", href: "/judges" },
  "poll-runs": { label: "poll panels", href: "/poll-runs" },
  comparisons: { label: "comparisons", href: "/comparisons" },
  datasets: { label: "datasets", href: "/datasets" },
  annotations: { label: "annotations", href: "/annotations" },
  feedback: { label: "feedback", href: "/feedback" },
  prompts: { label: "prompts", href: "/prompts" },
  playground: { label: "playground", href: "/playground" },
  studio: { label: "studio", href: "/studio" },
  "api-keys": { label: "api keys", href: "/api-keys" },
  members: { label: "members", href: "/members" },
  workspace: { label: "workspace", href: "/workspace" },
};

const WORKSPACE_SUBPAGES: Record<string, string> = {
  credentials: "llm credentials",
  sso: "sso",
};

function looksLikeId(segment: string): boolean {
  return /^[0-9a-f-]{8,}$/i.test(segment) || segment.length > 24;
}

export default function AutoCrumbs({
  projectSlug,
}: {
  projectSlug: string | null;
}) {
  const pathname = usePathname() ?? "/";
  const segments = pathname.split("/").filter(Boolean);
  const first = segments[0] ?? "";
  const section = SECTION_LABELS[first] ?? { label: first, href: `/${first}` };
  const detail = segments[1];

  const detailLabel = detail
    ? first === "workspace"
      ? (WORKSPACE_SUBPAGES[detail] ?? detail)
      : looksLikeId(detail)
        ? detail.slice(0, 8)
        : detail
    : null;

  return (
    <>
      {projectSlug ? (
        <>
          <span className="mono">{projectSlug}</span>
          <span className="sep">/</span>
        </>
      ) : null}
      {detailLabel ? (
        <>
          <Link href={section.href}>{section.label}</Link>
          <span className="sep">/</span>
          <span className={`last${looksLikeId(detail!) ? " mono" : ""}`}>
            {detailLabel}
          </span>
        </>
      ) : (
        <span className="last">{section.label}</span>
      )}
    </>
  );
}
