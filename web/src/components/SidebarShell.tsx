"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { LogoutLink } from "@/components/AuthClient";
import type { Project } from "@/lib/projects";
import {
  type MeResponse,
  SIDEBAR_COLLAPSED_VALUE,
  SIDEBAR_COOKIE,
  SIDEBAR_EXPANDED_VALUE,
} from "@/lib/sidebar";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { SidebarNav } from "./SidebarNav";

/**
 * Collapsible sidebar column. Expanded = icon + label pills at
 * var(--sidebar-w); collapsed = icon rail at var(--sidebar-w-rail)
 * with title tooltips. The choice persists in a cookie so the server
 * render (Shell reads it via next/headers) matches the client and
 * navigation never flickers.
 */

// Cookie contract + MeResponse live in @/lib/sidebar (a plain module) so
// the server-side read in Shell.tsx imports real string values, not
// client-reference proxies.

export function SidebarShell({
  active,
  projects,
  me,
  initialCollapsed,
}: {
  active: Project | null;
  projects: Project[];
  me: MeResponse | null;
  initialCollapsed: boolean;
}) {
  const [userCollapsed, setUserCollapsed] = useState(initialCollapsed);
  // Narrow viewports force the icon rail — a 224px sidebar in a 375px
  // window leaves no room for the actual product. The user's cookie
  // choice is preserved and reapplies when the window grows back.
  const [forcedRail, setForcedRail] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const apply = () => setForcedRail(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
  const collapsed = userCollapsed || forcedRail;

  function toggle() {
    const next = !collapsed;
    setUserCollapsed(next);
    const secure = window.location.protocol === "https:" ? "; secure" : "";
    document.cookie = `${SIDEBAR_COOKIE}=${next ? SIDEBAR_COLLAPSED_VALUE : SIDEBAR_EXPANDED_VALUE}; path=/; max-age=31536000; samesite=lax${secure}`;
  }

  return (
    <aside
      style={{
        width: collapsed ? "var(--sidebar-w-rail)" : "var(--sidebar-w)",
        background: "var(--surface-sidebar)",
        borderRight: "1px solid var(--border-soft)",
        display: "flex",
        flexDirection: "column",
        padding: collapsed ? "20px 10px 14px" : "20px 14px 14px",
        overflow: "hidden",
        transition: "width var(--motion-base) var(--ease-out), padding var(--motion-base) var(--ease-out)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: collapsed ? "column" : "row",
          alignItems: "center",
          gap: collapsed ? 10 : 4,
        }}
      >
        <BrandMark collapsed={collapsed} />
        {collapsed ? null : <span style={{ flex: 1 }} />}
        <button
          type="button"
          onClick={toggle}
          className="sidebar-toggle"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} strokeWidth={1.5} aria-hidden />
          ) : (
            <PanelLeftClose size={16} strokeWidth={1.5} aria-hidden />
          )}
        </button>
      </div>
      {collapsed ? null : (
        <div style={{ marginTop: 18 }}>
          <ProjectSwitcher active={active} projects={projects} />
        </div>
      )}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          marginTop: 8,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <SidebarNav collapsed={collapsed} />
      </div>
      <SidebarFooter me={me} collapsed={collapsed} />
    </aside>
  );
}

function BrandMark({ collapsed }: { collapsed: boolean }) {
  return (
    <Link
      href="/"
      aria-label="langprobe home"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 9,
        padding: collapsed ? 0 : "0 8px",
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
          flexShrink: 0,
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
      {collapsed ? null : (
        <span
          style={{
            fontSize: 15,
            fontWeight: 800,
            letterSpacing: "-0.01em",
            color: "var(--text)",
            whiteSpace: "nowrap",
          }}
        >
          langprobe
        </span>
      )}
    </Link>
  );
}

function SidebarFooter({
  me,
  collapsed,
}: {
  me: MeResponse | null;
  collapsed: boolean;
}) {
  if (me === null) {
    if (collapsed) {
      return (
        <div
          style={{
            padding: "14px 0 2px",
            borderTop: "1px solid var(--border-soft)",
            display: "flex",
            justifyContent: "center",
          }}
        >
          <Link
            href="/login"
            className="btn btn-ghost btn-sm"
            title="Sign in"
            aria-label="Sign in"
            style={{ padding: "0 8px" }}
          >
            <span aria-hidden>→</span>
          </Link>
        </div>
      );
    }
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
  const avatar = (
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
  );
  if (collapsed) {
    return (
      <div
        style={{
          padding: "14px 0 2px",
          borderTop: "1px solid var(--border-soft)",
          display: "flex",
          justifyContent: "center",
        }}
        title={me.email}
      >
        {avatar}
      </div>
    );
  }
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
      {avatar}
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
