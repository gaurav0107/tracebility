# PRODUCT.md

> Source of truth for what langprobe is, who it's for, and the
> design register the product follows. Read by `impeccable` and any
> contributor onboarding the project.

## What it is

**Langprobe is the real debugger for agents.** Trace, replay, and
eval every signal — runs, OTel envelopes, replays, evals, end-user
feedback — in one observability surface.

The product wedge: when an LLM agent goes sideways at 2 a.m., this
is the tool you reach for. Replay the run with edits applied,
compare A/B prompts against a real model, score every item against
LLM-as-judge or panel-of-LLM-judges.

**Agent-first.** The surface is built for agents, not only humans.
Every read is available as a token-budgeted, LLM-legible projection
over REST and MCP, so an agent can debug an agent: find the failed
run, read its salient slice, replay an edit, read the diff. Replay →
diff → eval is the agent's debug loop, and it's what the incumbents
(human dashboards on proprietary or storage-war backends) don't do.

## One-line pitches

- **Hero:** The real debugger for agents.
- **Subhead:** Trace, replay, eval — every signal in one place.

## Register

`product` — design SERVES the product. UI is dense, ergonomic,
low-decoration. Aesthetic peers: Linear, Datadog, Vercel dashboard,
GitHub Primer's quieter views. Anti-peers: LangSmith / Braintrust
landing pages, generic SaaS-template marketing.

The /login surface is the one exception: it's the first impression
and gets a hero scene. Everything else (dashboards, runs, evals,
playground, studio) holds the product register strictly.

## Audience

Engineers and eval-rigor teams, equally.

- **Engineers** fighting an agent at 2 a.m. Want speed, mono numbers,
  keyboard shortcuts, deep-link URLs. Hate fluff.
- **DS / ML researchers** building eval suites, A/B comparisons,
  judge panels. Tolerate higher density, want charts and drill-down.

Both share: dark-tolerant, prefer information density to whitespace,
read mono numbers. Both reward keyboard shortcuts; both punish
modal-heavy flows.

## Deployment posture

Langprobe ships **two postures from the same codebase**:

1. **Self-hosted (today).** Operator runs `docker compose up`. Data
   stays in their VPC. Apache-2.0. Per-instance is the deployment
   model. The current `/v1/setup` wizard, helm chart, and operator
   target this.
2. **SaaS, multi-tenant (planned, on roadmap).** A central langprobe
   instance hosted by us, where each customer signs up via OAuth
   (Google / GitHub) and lands in their own workspace. Same UI, same
   API surface, but workspaces are isolated tenants on shared infra.

Implication for design: the chrome must look credible in **both**
postures. No "self-hosted" branding bolted into the hero. The
`/login` page already supports both via OAuth signup; the SaaS gate
is roadmap, not a separate product.

## TODO (multi-tenancy, SaaS gate)

Captured here so it's not lost:

- [ ] Multi-tenancy at the data plane (per-tenant ClickHouse isolation
      or row-level scoping with proven query plans)
- [ ] Per-tenant rate limits + cost ceilings on /v1/runs
- [ ] Billing meters: tokens stored, runs ingested, eval-runs
- [ ] Stripe-style billing UI under /workspace/billing
- [ ] Public registration page deployed at app.langprobe.ai (or
      similar) that points at the SaaS instance
- [ ] Remove "self-hosted" microcopy from the auth surface and the
      footer when SaaS is the target deployment

## What it is NOT

- Not a generic APM tool. The product is opinionated about LLM /
  agent semantics: prompts, completions, tools, replays, judges.
- Not a marketing site. The hero scene on /login is product
  positioning, not a landing page. There's no separate marketing
  domain in scope.
- Not a chat product. We're observability + eval for chat / agent
  products, not the product itself.
- Not LangSmith with our logo. The wedge is "real debugger" — that
  is, replay + studio + studio-replay + workspace-scoped LLM
  credentials with reveal-once UX. Things LangSmith doesn't do as
  well or doesn't do at all.

## Tone

- **Voice:** confident, specific, terse. "The real debugger for
  agents" is the canonical line; everything else should match its
  shape.
- **No marketing buzzwords.** No streamline / empower / supercharge /
  leverage / enterprise-grade / next-generation / cutting-edge.
- **No aphoristic cadence.** Don't fall into "serious statement,
  punchy short negation" three times in a row.
- **Mono numbers everywhere.** Latency, tokens, cost, run-ids,
  span-ids — all tabular Geist Mono, not proportional.

## Design references

- Visual reference: `/Users/mia/Downloads/langprobe.html` (the
  approved aesthetic).
- Design system: `DESIGN.md` is the source of truth for tokens,
  fonts, spacing, motion. Never deviate without explicit approval.
- Aesthetic peers: Linear, Vercel dashboard, GitHub Primer (quieter
  views). NOT: LangSmith / Braintrust landing pages, glassmorphism
  SaaS templates.
