"use client";

import {
  Activity,
  BookText,
  Boxes,
  Database,
  FlaskConical,
  Gauge,
  Gavel,
  GitCompare,
  KeyRound,
  ListTree,
  type LucideIcon,
  MessageSquare,
  PenLine,
  RotateCcw,
  Settings,
  SquareTerminal,
  Users,
  Vote,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Extra path prefixes that keep this item lit (merged surfaces). */
  also?: string[];
};
type NavSection = { label: string; items: NavItem[] };

// Icon + label pills, Lucide 16px stroke per DESIGN.md iconography.
// Tracing owns /runs + /threads (the /threads/[session_id] detail route
// survives the merge); Monitoring also claims /alerts, which today is
// only a redirect — kept so any future /alerts subroute lights the
// right nav item.
const SECTIONS: NavSection[] = [
  {
    label: "Observe",
    items: [
      { label: "Overview", href: "/", icon: Gauge },
      { label: "Tracing", href: "/runs", icon: ListTree, also: ["/threads"] },
      {
        label: "Monitoring",
        href: "/monitoring",
        icon: Activity,
        also: ["/alerts"],
      },
      { label: "Replay", href: "/replay", icon: RotateCcw },
    ],
  },
  {
    label: "Evaluate",
    items: [
      { label: "Evals", href: "/evals", icon: FlaskConical },
      { label: "Judges", href: "/judges", icon: Gavel },
      { label: "PoLL panels", href: "/poll-runs", icon: Vote },
      { label: "Comparisons", href: "/comparisons", icon: GitCompare },
      { label: "Datasets", href: "/datasets", icon: Database },
      { label: "Annotations", href: "/annotations", icon: PenLine },
      { label: "Feedback", href: "/feedback", icon: MessageSquare },
    ],
  },
  {
    label: "Build",
    items: [
      { label: "Prompts", href: "/prompts", icon: BookText },
      { label: "Playground", href: "/playground", icon: SquareTerminal },
      { label: "Studio", href: "/studio", icon: Boxes },
    ],
  },
  {
    label: "Settings",
    items: [
      { label: "API keys", href: "/api-keys", icon: KeyRound },
      { label: "Members", href: "/members", icon: Users },
      { label: "Workspace", href: "/workspace", icon: Settings },
    ],
  },
];

export function SidebarNav({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname() ?? "/";
  return (
    <>
      {SECTIONS.map((section, i) => (
        <div
          key={section.label}
          style={{ display: "flex", flexDirection: "column", gap: 1 }}
        >
          {collapsed ? (
            i > 0 ? (
              <div className="nav-section-rule" aria-hidden />
            ) : null
          ) : (
            <div className="nav-section-label">{section.label}</div>
          )}
          {section.items.map((item) => {
            const active = isActive(pathname, item);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-item${active ? " active" : ""}${collapsed ? " nav-item-collapsed" : ""}`}
                aria-current={active ? "page" : undefined}
                aria-label={item.label}
                title={collapsed ? item.label : undefined}
              >
                <Icon className="nav-icon" size={16} strokeWidth={1.5} aria-hidden />
                {collapsed ? null : <span>{item.label}</span>}
              </Link>
            );
          })}
        </div>
      ))}
    </>
  );
}

function isActive(pathname: string, item: NavItem): boolean {
  const matches = (href: string): boolean => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };
  if (matches(item.href)) return true;
  return (item.also ?? []).some(matches);
}
