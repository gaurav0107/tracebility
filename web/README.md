# web

Next.js 14 (App Router) + TypeScript shell for the langprobe product UI.
Boots on port 7090 to match the API CORS allowlist
(`LANGPROBE_CORS_ALLOW_ORIGIN=http://localhost:7090`).

Visual language is locked in repo-root `DESIGN.md`. Tokens live in
`src/app/globals.css`. **No blue, no purple, no gradients.** If you find
yourself reaching for `box-shadow`, reach for a 1px `--rule` border instead.

## Run

```sh
pnpm install
pnpm --filter @langprobe/web dev
```

Then open http://localhost:7090.

## Design conflict to resolve

The mock at `file:///Users/mia/Downloads/langprobe.html` uses a blue accent
(#2056E2). DESIGN.md (locked 2026-05-25) calls for amber-orange (#D9531E /
#E96A2E). This scaffold follows DESIGN.md. If we want to revisit, run
`/design-consultation` and update DESIGN.md before changing the tokens.

## Structure

```
src/
  app/
    layout.tsx       — root layout
    page.tsx         — overview dashboard
    globals.css      — design tokens + reset
    runs/            — unified Tracing surface (Threads | Traces switcher + stats rail)
    monitoring/      — Dashboards | Alerts tabs (alert rules live beside their charts)
  components/
    Shell.tsx        — app chrome: sidebar column + main column (topbar + scroll);
                       reads the `lp_sidebar` cookie server-side so SSR matches client
    SidebarShell.tsx — client sidebar: brand row + collapse toggle, project switcher,
                       icon nav; collapses to a 58px icon rail
    SidebarNav.tsx   — nav items grouped Observe / Evaluate / Build / Settings
  lib/
    sidebar.ts       — `lp_sidebar` cookie contract shared by server read + client write
```

Route notes: `/threads` and `/alerts` are redirect stubs — they 307 to
`/runs?view=threads` and `/monitoring?tab=alerts` so old links and bookmarks
keep working (`/threads/[session_id]` remains the per-session drill-down).
