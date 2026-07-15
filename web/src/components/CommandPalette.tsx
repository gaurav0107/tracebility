"use client";

/**
 * ⌘K command palette. Replaces the decorative topbar search with a
 * real one: navigation actions + live search over runs / prompts /
 * datasets / eval runs (via /api/search, one round trip, debounced).
 *
 * Keyboard contract (DESIGN.md accessibility): ⌘K / Ctrl+K toggles,
 * ↑/↓ move, Enter opens, Escape closes. The trigger renders in the
 * topbar styled as the familiar `.search-box`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

interface RunHit {
  run_id: string;
  name: string;
  kind: string;
  status: string;
}
interface SlugHit {
  id: string;
  slug: string;
  name: string;
}
interface EvalHit {
  id: string;
  name: string | null;
  status: string;
}

interface SearchPayload {
  runs: RunHit[];
  prompts: SlugHit[];
  datasets: SlugHit[];
  evals: EvalHit[];
}

interface PaletteItem {
  key: string;
  group: "Pages" | "Runs" | "Prompts" | "Datasets" | "Eval runs";
  label: string;
  hint?: string;
  mono?: boolean;
  href: string;
}

const PAGES: { label: string; href: string; hint?: string }[] = [
  { label: "Overview", href: "/" },
  { label: "Tracing — traces", href: "/runs", hint: "runs" },
  { label: "Tracing — threads", href: "/runs?view=threads", hint: "sessions" },
  { label: "Monitoring", href: "/monitoring" },
  { label: "Alerts", href: "/monitoring?tab=alerts" },
  { label: "Replay", href: "/replay" },
  { label: "Evals", href: "/evals" },
  { label: "Judges", href: "/judges" },
  { label: "PoLL panels", href: "/poll-runs" },
  { label: "Comparisons", href: "/comparisons" },
  { label: "Datasets", href: "/datasets" },
  { label: "Annotations", href: "/annotations" },
  { label: "Feedback", href: "/feedback" },
  { label: "Prompts", href: "/prompts" },
  { label: "Playground", href: "/playground" },
  { label: "Studio", href: "/studio" },
  { label: "API keys", href: "/api-keys" },
  { label: "Members", href: "/members" },
  { label: "Workspace settings", href: "/workspace" },
  { label: "LLM credentials", href: "/workspace/credentials" },
];

export default function CommandPalette({
  projectId,
}: {
  projectId: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  // Global shortcut.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQ("");
      setHits(null);
      setCursor(0);
      // Focus after the overlay paints.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Debounced fetch.
  useEffect(() => {
    if (!open || !projectId) return;
    const mySeq = ++seq.current;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(q)}`,
        );
        if (!res.ok) return;
        const data = (await res.json()) as SearchPayload;
        if (seq.current === mySeq) {
          setHits(data);
          setCursor(0);
        }
      } finally {
        if (seq.current === mySeq) setLoading(false);
      }
    }, 180);
    return () => clearTimeout(t);
  }, [open, q, projectId]);

  const items = useMemo<PaletteItem[]>(() => {
    const needle = q.trim().toLowerCase();
    const out: PaletteItem[] = [];
    for (const p of PAGES) {
      if (!needle || p.label.toLowerCase().includes(needle)) {
        out.push({
          key: `page:${p.href}`,
          group: "Pages",
          label: p.label,
          hint: p.hint,
          href: p.href,
        });
      }
    }
    // Cap pages when searching so data hits stay above the fold.
    const pages = needle ? out.slice(0, 3) : out.slice(0, 6);
    const rest: PaletteItem[] = [];
    for (const r of hits?.runs ?? []) {
      rest.push({
        key: `run:${r.run_id}`,
        group: "Runs",
        label: r.name,
        hint: `${r.run_id.slice(0, 8)} · ${r.status}`,
        mono: true,
        href: `/runs/${r.run_id}`,
      });
    }
    for (const p of hits?.prompts ?? []) {
      rest.push({
        key: `prompt:${p.id}`,
        group: "Prompts",
        label: p.name,
        hint: p.slug,
        href: `/prompts/${p.id}`,
      });
    }
    for (const d of hits?.datasets ?? []) {
      rest.push({
        key: `dataset:${d.id}`,
        group: "Datasets",
        label: d.name,
        hint: d.slug,
        href: `/datasets/${d.id}`,
      });
    }
    for (const e of hits?.evals ?? []) {
      rest.push({
        key: `eval:${e.id}`,
        group: "Eval runs",
        label: e.name || e.id.slice(0, 8),
        hint: e.status,
        href: `/evals/${e.id}`,
      });
    }
    return [...pages, ...rest];
  }, [q, hits]);

  const go = useCallback(
    (item: PaletteItem) => {
      setOpen(false);
      router.push(item.href);
    },
    [router],
  );

  function onInputKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = items[cursor];
      if (item) go(item);
    }
  }

  // Keep the active row visible while arrowing.
  useEffect(() => {
    const el = listRef.current?.querySelector('[data-active="true"]');
    el?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  let lastGroup: string | null = null;

  return (
    <>
      <button
        type="button"
        className="search-box"
        style={{
          maxWidth: 300,
          width: "100%",
          cursor: "pointer",
          border: "1px solid var(--border)",
          background: "var(--surface-2)",
        }}
        onClick={() => setOpen(true)}
        aria-label="Search (⌘K)"
      >
        <span aria-hidden style={{ fontSize: 12, color: "var(--text-4)" }}>
          ⌕
        </span>
        <span
          style={{
            flex: 1,
            textAlign: "left",
            fontSize: 13,
            color: "var(--text-3)",
            overflow: "hidden",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
          }}
        >
          search runs, evals, prompts…
        </span>
        <span className="kbd" aria-hidden>
          ⌘K
        </span>
      </button>

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 80,
            background: "rgba(10,10,10,0.32)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            paddingTop: "12vh",
          }}
        >
          <div
            style={{
              width: 560,
              maxWidth: "calc(100vw - 32px)",
              background: "var(--surface)",
              border: "1px solid var(--border-strong)",
              borderRadius: "var(--r-4, 12px)",
              boxShadow: "var(--shadow-3)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "10px 14px",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <span aria-hidden style={{ color: "var(--text-4)", fontSize: 13 }}>
                ⌕
              </span>
              <input
                ref={inputRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={onInputKey}
                placeholder="search runs, prompts, datasets, evals — or jump to a page"
                aria-label="Search"
                style={{
                  flex: 1,
                  border: "none",
                  outline: "none",
                  background: "transparent",
                  fontSize: 14,
                  color: "var(--text)",
                }}
              />
              <span className="kbd">esc</span>
            </div>
            <div
              ref={listRef}
              style={{ maxHeight: 380, overflowY: "auto", padding: 6 }}
            >
              {items.length === 0 ? (
                <p
                  style={{
                    padding: "18px 12px",
                    margin: 0,
                    fontSize: 13,
                    color: "var(--text-3)",
                  }}
                >
                  {loading ? "searching…" : `no matches for “${q}”`}
                </p>
              ) : (
                items.map((item, i) => {
                  const showGroup = item.group !== lastGroup;
                  lastGroup = item.group;
                  return (
                    <div key={item.key}>
                      {showGroup ? (
                        <div
                          style={{
                            padding: "8px 10px 4px",
                            fontSize: 11,
                            fontWeight: 600,
                            textTransform: "uppercase",
                            letterSpacing: "0.04em",
                            color: "var(--text-3)",
                          }}
                        >
                          {item.group}
                        </div>
                      ) : null}
                      <button
                        type="button"
                        data-active={i === cursor}
                        onMouseEnter={() => setCursor(i)}
                        onClick={() => go(item)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          width: "100%",
                          textAlign: "left",
                          padding: "7px 10px",
                          border: "none",
                          borderRadius: "var(--r-2, 6px)",
                          cursor: "pointer",
                          background:
                            i === cursor ? "var(--accent-soft)" : "transparent",
                          color: "var(--text)",
                          fontSize: 13,
                        }}
                      >
                        <span
                          style={{
                            flex: 1,
                            overflow: "hidden",
                            whiteSpace: "nowrap",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {item.label}
                        </span>
                        {item.hint ? (
                          <span
                            className={item.mono ? "mono" : undefined}
                            style={{
                              fontSize: 11,
                              color: "var(--text-3)",
                              flexShrink: 0,
                            }}
                          >
                            {item.hint}
                          </span>
                        ) : null}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
            <div
              style={{
                display: "flex",
                gap: 12,
                padding: "8px 14px",
                borderTop: "1px solid var(--border)",
                fontSize: 11,
                color: "var(--text-3)",
              }}
            >
              <span>
                <span className="kbd">↑↓</span> navigate
              </span>
              <span>
                <span className="kbd">↵</span> open
              </span>
              <span>
                <span className="kbd">esc</span> close
              </span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
