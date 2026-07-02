# OSS + Cloud versions of langprobe — design

> Date: 2026-07-02
> Status: design approved in brainstorm; pending office-hours + CEO review.
> Author: brainstorm session (gaurav)

## Summary

langprobe ships **two deployment postures from one core codebase**:

1. **Open source, self-hosted** — operators deploy on their own infra. The
   full "real debugger for agents" (ingest, trace, replay, studio, evals) is
   Apache-2.0 and genuinely complete.
2. **Cloud, pay-per-use** — a central multi-tenant instance we host, where
   customers sign up and pay only for what they use.

This doc locks six strategic decisions and scopes the **first buildable
piece**: a pricing-agnostic **metering subsystem**. Actual prices are deferred
to later market research and live in config, not code.

The strategy in one line: **be more open than Langfuse, protect the cloud by
not shipping it, and undercut Langfuse ~3× on a familiar per-unit meter — with
replay/studio as the wedge no competitor has.**

## Competitive precedent (why these choices)

Researched 2026-07-02:

| Product | License | Self-host | Pricing model |
|---|---|---|---|
| **LangSmith** | Proprietary, closed | Enterprise-only, sales-led | Per-**trace** ($2.50/1k @14d, $5/1k @400d) **+ $39/seat** |
| **NeatLogs** | MIT **SDK only**; product closed | None | No public pricing; sales-led ("book a demo") |
| **Langfuse** | **MIT** core + `/ee` folder (license-key) | Free, unlimited, commercial-OK | Per-**unit** (span+event+score) $8/100k; **seats free**; retention = plan tier |

Key reads:
- Both **direct** competitors (LangSmith, NeatLogs) are **closed**. The one
  **open** competitor (Langfuse) wins the "LangSmith alternative" self-host
  market **by being maximally permissive (MIT)** — and has *not* been
  strip-mined. For an opinionated vertical dev tool, the AWS-rehost risk that
  justifies BSL/SSPL largely doesn't materialize. → out-open Langfuse, don't
  out-restrict it.
- LangSmith's **per-seat $39** is its most-resented axis; Langfuse beats it
  with **usage-only, seats free**. → langprobe never charges per seat.
- Langfuse sells **compliance + retention + support** on higher tiers, not more
  usage (usage is pure overage). → same shape.

## Decision 1 — License / open-core boundary

- **Apache-2.0 core** — the complete debugger. Fully self-hostable, no
  crippling. This is the adoption engine.
- **`/ee` folder inside the public repo** — source-available, license-key
  gated. Paid features a *single-tenant* company wants (SCIM, SAML, audit-log
  retention/export, project-level RBAC, data-retention policies). Mirrors
  Langfuse's `/ee` model.
- **Unpublished cloud layer** — the multi-tenant/billing/ops code is **never
  published**. A competitor cannot "rehost our cloud" because the cloud code
  isn't public; they'd have to rebuild tenant isolation, metering, billing, and
  ops themselves — at which point they're a real company, not a strip-miner.

Rejected: source-available relicense (BSL/SSPL/FSL) on the core. It would put
us on the *less-open* side of Langfuse (our only OSS competitor) and hand them
the "we're more open" line, while doing nothing about the *closed* competitors.
SSPL specifically is OSI-rejected and blocked by many corporate OSS policies.

## Decision 2 — Feature split (three buckets)

**Bucket 1 — Apache core (free, self-host, unlimited):**
- Ingest (OTel GenAI + LangSmith shim), trace/span view
- **Replay + Studio + studio-replay** (the wedge — must be free)
- Evals: LLM-as-judge, panel-of-judges, datasets, A/B prompt compare
- Projects, single-org RBAC, API keys, workspace-scoped LLM credentials
- Basic SSO (OIDC) — free, matching Langfuse
- `docker compose up`, helm chart, `/v1/setup` wizard
- **`migrate-langsmith` — free and loud.** The escape hatch from the incumbent
  is customer acquisition; never monetize it.

**Bucket 2 — `/ee` (paid, self-host, license-key):**
- SCIM provisioning, SAML, audit-log retention/export
- **Project-level / row-level RBAC** (single-org roles stay free; per-project
  access = you're a multi-team org that can pay)
- Data-retention policies, priority/LTS support

**Bucket 3 — Cloud-only (unpublished, pay-per-use):**
- Multi-tenant data-plane isolation
- Metering + Stripe billing, usage quotas / cost ceilings
- Public OAuth signup at app.langprobe.ai, hosted ops (backups, autoscale,
  upgrades)

**Principle:** monetize *scale* (cloud) and *enterprise-security* (`/ee`),
**never the wedge**. Replay/studio/eval stay free forever — they earn the
word-of-mouth that feeds both paid tiers.

## Decision 3 — Codebase structure

**Private cloud repo consumes the public core as a versioned dependency.**

- Public `langprobe/langprobe` — Apache core + `/ee`. Fully open.
- Private `langprobe/cloud` — depends on a pinned version of the public core,
  adds the multi-tenant layer, metering, billing, cloud auth.

The license boundary is **physically enforced**, not maintained by discipline:
cloud code never lives in the public repo, so it cannot leak. Matches how
Langfuse/Sentry/GitLab structure it. Clean extension interfaces (auth, tenancy,
usage) emerge naturally as the private repo forces the seams — we do **not**
over-design those interfaces up front.

## Decision 4 — Multi-tenant data-plane isolation

Data plane = ClickHouse (runs/spans/evals). Control plane = Postgres.

**Hybrid, one code path behind a tenancy-resolver interface:**
- **Row-level scoping (A)** — default for **self-serve pay-per-use**. Shared
  tables, `tenant_id` on every row. Economically supports thousands of small
  tenants.
- **Database-per-tenant (B)** — for **large enterprise** tenants who pay for
  stronger isolation, clean per-tenant export/delete, and noisy-neighbor
  boundaries. Same code path, different strategy behind the resolver.

Enforcement (defense-in-depth):
- Tenancy-resolver in the query layer that **structurally cannot emit an
  unscoped query** — the single thing that would leak data across tenants.
- ClickHouse **row policies + per-tenant quotas** as a second layer + noisy
  neighbor protection.

## Decision 5 — Metering (pay-per-use)

**Meter design (market-anchored on Langfuse's per-unit model):**
- **Primary meter:** units ingested = **spans + events + scores** (fine-grained,
  the modern standard — not per-top-level-trace like LangSmith).
- **Retention:** a **tier/multiplier on the unit** (standard included; extended
  90d/1yr+ costs more) — *not* a separate GB-month meter (simpler, matches the
  market).
- **Eval + replay compute:** a **metered add-on**. No competitor meters this
  because none has replay — it's a differentiated, high-willingness-to-pay line
  item unique to our wedge.
- **Free tier:** generous (working placeholder ~100k units/mo, 30-day
  retention), **unlimited seats**, no credit card.
- **No mandatory monthly floor** for self-serve (differentiates from Langfuse's
  $29 Core). Optional flat Team/Enterprise plan bundles compliance + long
  retention + support (where Langfuse actually makes money).
- **Never per-seat.** The headline anti-LangSmith wedge.

**Pricing numbers are DEFERRED** to later market research and live in config.
Working placeholder for planning only: **~3× under Langfuse** (~$2.50/100k
units vs their $8/100k). Margin caveat to validate in CEO review: confirm
~$2.50/100k clears ClickHouse COGS at volume.

## Decision 6 — Auth & onboarding

**Shared identity core; two entry points via the seam.**
- Core ships OIDC + the `/v1/setup` wizard (Apache). Self-host path unchanged.
- Cloud's private repo implements the same auth interface with Google/GitHub
  OAuth + public signup + Stripe billing gate as plugins.
- One identity model, two doors (`/v1/setup` vs `app.langprobe.ai`). Avoids the
  two-auth-stack drift where security bugs live.

(Low-cost and revisitable; default choice, consistent with Decision 3.)

## First build — the metering subsystem (implementation scope)

Everything above is the strategic frame. The **first buildable spec** is the
metering engine, built **pricing-agnostic** so deferred prices are a config
edit, not a rebuild. Lives in the **cloud** repo; consumes a thin generic
**usage-event emission seam** added to the Apache core (self-host can no-op it
or use it for local usage stats).

### Components

1. **Usage capture (seam in core, consumed in cloud).**
   Every ingest path (spans, events, scores) emits a metered
   `usage_event { tenant_id, unit_type, count, retention_tier, ts }`,
   instrumented **once** at the ingest-api / ingest-worker boundary so nothing
   can bypass it. In core the seam is a generic hook; cloud supplies the
   metering implementation.

2. **Aggregation.**
   Roll usage into per-tenant, per-period counters in ClickHouse — queryable
   for both dashboards and billing. `retention_tier` is a dimension on the unit.

3. **Rating engine (config-driven).**
   A pricing **config** (per-unit rates, free-tier allotments, retention
   multipliers, eval/replay rates, volume graduations) turns raw usage into a
   priced bill. **Prices live in config, not code.** "Real pricing later" = a
   config change.

4. **Quota / enforcement hooks.**
   Per-tenant free-tier + cost-ceiling checks on the ingest path (the
   `PRODUCT.md` "per-tenant rate limits + cost ceilings" TODO), returning clean
   429s when exceeded.

5. **Usage read surface.**
   A read API backing a `/workspace/usage` view (what the customer sees).
   Stripe wiring is **deferred** behind the same rating output — the rating
   engine produces the priced line items; the payment integration consumes them
   later.

### Explicitly out of scope for the first spec
- Actual price numbers (deferred to market research; config placeholders only).
- Stripe/payment integration (rating output is built to feed it later).
- The full multi-tenant data plane (metering assumes `tenant_id` exists; the
  tenancy-resolver is a parallel workstream).
- `/ee` license-key mechanism, SCIM/SAML, cloud OAuth signup.

## Interfaces / seams introduced

- **Usage-event seam** (core → cloud): generic emission hook at ingest
  boundary. Self-host no-ops or uses for local stats.
- **Tenancy-resolver** (cloud): structurally-unbypassable query scoping;
  strategy = row-level (self-serve) or db-per-tenant (enterprise). *Parallel
  workstream; metering depends only on `tenant_id` being present.*
- **Auth interface** (core → cloud): OIDC/`/v1/setup` in core; OAuth+Stripe in
  cloud. *Parallel workstream.*

## Open questions for office-hours / CEO review

1. **Margin:** does ~3× under Langfuse (~$2.50/100k units) clear ClickHouse
   COGS at volume? (Placeholder only, but the wedge depends on it being viable.)
2. **Free-tier size:** ~100k units/mo is a placeholder — validate against CAC /
   self-host→cloud funnel conversion.
3. **Sequencing:** metering first vs tenancy-resolver first — both are cloud
   prerequisites; which unblocks a demoable cloud slice soonest?
4. **`/ee` timing:** does the license-key mechanism matter before cloud, or can
   it wait until there's paid self-host demand?
