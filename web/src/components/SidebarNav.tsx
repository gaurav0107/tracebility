"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { label: string; href: string };
type NavSection = { label: string; items: NavItem[] };

// Text-only pill nav, per the design overhaul (no icons). Routes are the real
// app routes — the target canvas's nav list is illustrative, we keep every page.
const SECTIONS: NavSection[] = [
  {
    label: "Observe",
    items: [
      { label: "Overview", href: "/" },
      { label: "Traces", href: "/runs" },
      { label: "Threads", href: "/threads" },
      { label: "Monitoring", href: "/monitoring" },
      { label: "Alerts", href: "/alerts" },
      { label: "Replay", href: "/replay" },
    ],
  },
  {
    label: "Improve",
    items: [
      { label: "Evals", href: "/evals" },
      { label: "PoLL panels", href: "/poll-runs" },
      { label: "Judges", href: "/judges" },
      { label: "Comparisons", href: "/comparisons" },
      { label: "Datasets", href: "/datasets" },
      { label: "Prompts", href: "/prompts" },
      { label: "Playground", href: "/playground" },
      { label: "Annotations", href: "/annotations" },
      { label: "Feedback", href: "/feedback" },
      { label: "Studio", href: "/studio" },
    ],
  },
  {
    label: "Settings",
    items: [
      { label: "API keys", href: "/api-keys" },
      { label: "Members", href: "/members" },
      { label: "Workspace", href: "/workspace" },
    ],
  },
];

export function SidebarNav() {
  const pathname = usePathname() ?? "/";
  return (
    <>
      {SECTIONS.map((section) => (
        <div key={section.label} style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <div className="nav-section-label">{section.label}</div>
          {section.items.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-item${active ? " active" : ""}`}
              >
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </>
  );
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
