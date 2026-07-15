/**
 * App chrome — "Langprobe App Design Overhaul" (see .context/design-target/SPEC.md):
 *   [ sidebar (collapsible, full height) ][ main (topbar 58 + scroll) ]
 *
 * Sidebar (--surface-sidebar) is a client component (SidebarShell):
 * brand row + collapse toggle · project-switcher pill · icon nav ·
 * footer user-row. Collapse persists in the `lp_sidebar` cookie so
 * the server render matches the client. Topbar lives INSIDE the main
 * column (breadcrumb + search pill). Children get the full interior —
 * pages decide their own pad.
 */

import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { apiGet } from "@/lib/api";
import type { Project } from "@/lib/projects";
import {
  type MeResponse,
  SIDEBAR_COLLAPSED_VALUE,
  SIDEBAR_COOKIE,
} from "@/lib/sidebar";
import AutoCrumbs from "./AutoCrumbs";
import CommandPalette from "./CommandPalette";
import { SidebarShell } from "./SidebarShell";

export async function Shell({
  children,
  active,
  projects,
  crumbs,
  inspector,
}: {
  children: ReactNode;
  active: Project | null;
  projects: Project[];
  crumbs?: ReactNode;
  inspector?: ReactNode;
}) {
  const meRes = await apiGet<MeResponse>("/v1/auth/me");
  const me = meRes.data;
  const collapsed =
    cookies().get(SIDEBAR_COOKIE)?.value === SIDEBAR_COLLAPSED_VALUE;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        height: "100vh",
        background: "var(--bg)",
        overflow: "hidden",
      }}
    >
      <SidebarShell
        active={active}
        projects={projects}
        me={me}
        initialCollapsed={collapsed}
      />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          background: "var(--surface-app)",
          overflow: "hidden",
        }}
      >
        <Topbar crumbs={crumbs} active={active} />
        <main
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            background: "var(--surface-app)",
          }}
        >
          {children}
          {inspector}
        </main>
      </div>
    </div>
  );
}

function Topbar({
  crumbs,
  active,
}: {
  crumbs?: ReactNode;
  active: Project | null;
}) {
  return (
    <header
      style={{
        height: "var(--topbar-h)",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "0 32px",
        borderBottom: "1px solid var(--border-soft)",
        background: "var(--surface-app)",
      }}
    >
      <div className="crumbs">
        {crumbs ?? <AutoCrumbs projectSlug={active?.slug ?? null} />}
      </div>
      <div style={{ flex: 1 }} />
      <CommandPalette projectId={active?.id ?? null} />
    </header>
  );
}
