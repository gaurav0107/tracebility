/**
 * App chrome — "Langprobe App Design Overhaul" (see .context/design-target/SPEC.md):
 *   [ sidebar (236, full height) ][ main (topbar 58 + scroll) ]
 *
 * Sidebar (--surface-sidebar): brand row · project-switcher pill · nav (text
 * pills) · footer user-row. Topbar lives INSIDE the main column (breadcrumb +
 * search pill). Children get the full interior — pages decide their own pad.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import { LogoutLink } from "@/components/AuthClient";
import { apiGet } from "@/lib/api";
import type { Project } from "@/lib/projects";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { SidebarNav } from "./SidebarNav";

interface MeResponse {
  user_id: string;
  email: string;
  is_root: boolean;
}

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
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "var(--sidebar-w) 1fr",
        height: "100vh",
        background: "var(--bg)",
        overflow: "hidden",
      }}
    >
      <Sidebar active={active} projects={projects} me={me} />
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
        {crumbs ?? (
          <>
            {active ? <span className="mono">{active.slug}</span> : null}
            <span className="sep">/</span>
            <span className="last">overview</span>
          </>
        )}
      </div>
      <div style={{ flex: 1 }} />
      <SearchBox />
    </header>
  );
}

function SearchBox() {
  return (
    <label className="search-box" htmlFor="topbar-search" style={{ maxWidth: 300 }}>
      <span aria-hidden style={{ fontSize: 12, color: "var(--text-4)" }}>
        ⌕
      </span>
      <input
        id="topbar-search"
        type="search"
        placeholder="search runs, evals, prompts…"
        aria-label="Search"
      />
      <span className="kbd" aria-hidden>
        ⌘K
      </span>
    </label>
  );
}

function Sidebar({
  active,
  projects,
  me,
}: {
  active: Project | null;
  projects: Project[];
  me: MeResponse | null;
}) {
  return (
    <aside
      style={{
        background: "var(--surface-sidebar)",
        borderRight: "1px solid var(--border-soft)",
        display: "flex",
        flexDirection: "column",
        padding: "20px 14px 14px",
        overflow: "hidden",
      }}
    >
      <BrandMark />
      <div style={{ marginTop: 18 }}>
        <ProjectSwitcher active={active} projects={projects} />
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          marginTop: 8,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <SidebarNav />
      </div>
      <SidebarFooter me={me} />
    </aside>
  );
}

function BrandMark() {
  return (
    <Link
      href="/"
      aria-label="langprobe home"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 9,
        padding: "0 8px",
        textDecoration: "none",
        color: "var(--text)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 24,
          height: 24,
          borderRadius: 8,
          background: "var(--accent)",
          display: "grid",
          placeItems: "center",
          boxShadow: "var(--shadow-logo)",
        }}
      >
        <span
          style={{
            width: 9,
            height: 9,
            borderRadius: 3,
            background: "#FFFFFF",
          }}
        />
      </span>
      <span
        style={{
          fontSize: 15,
          fontWeight: 800,
          letterSpacing: "-0.01em",
          color: "var(--text)",
        }}
      >
        langprobe
      </span>
    </Link>
  );
}

function SidebarFooter({ me }: { me: MeResponse | null }) {
  if (me === null) {
    return (
      <div
        style={{
          padding: "14px 8px 2px",
          borderTop: "1px solid var(--border-soft)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Link
          href="/login"
          className="btn btn-primary btn-sm"
          style={{ flex: 1 }}
        >
          Sign in
        </Link>
        <Link href="/login?tab=signup" className="btn btn-ghost btn-sm">
          Sign up
        </Link>
      </div>
    );
  }
  const initial = (me.email || "?").charAt(0).toLowerCase();
  return (
    <div
      style={{
        padding: "14px 8px 2px",
        borderTop: "1px solid var(--border-soft)",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 28,
          height: 28,
          borderRadius: 9999,
          background: "var(--surface-3)",
          color: "var(--text-2)",
          display: "grid",
          placeItems: "center",
          fontSize: 12,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {initial}
      </span>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          flex: 1,
        }}
      >
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {me.email}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 10.5,
            color: "var(--text-4)",
          }}
        >
          {me.is_root ? "root" : "member"}
        </span>
      </div>
      <LogoutLink />
    </div>
  );
}
