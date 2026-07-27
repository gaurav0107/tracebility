# Editions separation — core / enterprise / cloud (design)

> Date: 2026-07-26
> Status: implements issue #36 (part of epic #34). Strategy inherited from
> `2026-07-02-oss-and-cloud-versions-design.md` (locked); this doc turns that
> strategy into the concrete boundary: router-by-router allocation,
> extension-point architecture, dependency rules, migration sequence — plus
> pricing tiers, support tiers, and the compliance runway.
> Author: spec session (gaurav)

## The model (recap, locked in #34)

| Layer | Repo | License | Distribution |
|---|---|---|---|
| 🟢 **Core** | `langprobe/langprobe` | Apache-2.0 | public |
| 🟡 **Enterprise** | `langprobe/langprobe-enterprise` (new) | source-available, commercial, license-key | public repo + private PyPI + enterprise docker image |
| 🔴 **Cloud** | `langprobe/langprobe-cloud` (new, **private**) | proprietary | consumes core+enterprise image |

Non-negotiables carried forward:

- Grafana-style **plugin**, not a GitLab `/ee` folder. Enterprise attaches to
  core via extension points; if a feature must fork core internals, the plugin
  model is failing — stop and reconsider before shipping it.
- **Never monetize the wedge.** Replay, studio, evals, migrate-langsmith stay
  Apache forever.
- **Never per-seat.** Metering/billing are cloud-only; self-host
  (core, or core+enterprise) is unmetered and never phones home.
- Dependency rule: core → nothing; enterprise → core; cloud → core+enterprise.
  CI-enforced (#44), not conventional.

## 1. Feature allocation — router by router

Every FastAPI router in `services/api/langprobe_api/routers/` (34 files),
allocated. "Core" = stays where it is. "Enterprise"/"Cloud" = extracted to the
new repo and re-attached via extension points.

### Core (Apache) — 31 routers

| Router | Why it stays core |
|---|---|
| `agent_views.py` | Agent-first surface — the moat, free. |
| `alerts.py` | Monitoring parity, table stakes. |
| `annotations.py` | Human-review queues, eval loop substrate. |
| `api_keys.py` | Self-host auth basics. |
| `auth.py` | Login/logout/me — one identity core (Decision 6). |
| `comparisons.py` | A/B compare — the wedge. |
| `datasets.py` | Eval substrate. |
| `evals.py` | The wedge. |
| `feedback.py`, `feedback_keys.py` | End-user feedback ingest, free signal capture. |
| `health.py` | Ops basics. |
| `llm_credentials.py` | Workspace LLM creds, needed by replay/evals. |
| `luna_judges.py` | LLM-as-judge — the wedge. |
| `members.py` | Single-org RBAC stays free (Decision 2). |
| `metrics.py` | Overview roll-ups. |
| `playground.py` | The wedge. |
| `poll_runs.py` | Panel-of-judges — the wedge. |
| `projects.py` | Org model stays OSS (#40). |
| `prompts.py` | Prompt CRUD free; *protected prompt labels* gate → enterprise (see below). |
| `reliability.py` | Eval-rigor read surface. |
| `replay_runs.py`, `replays.py` | THE wedge. |
| `run_actions.py`, `runs_query.py`, `threads_query.py`, `saved_views.py` | Trace/debug surface. |
| `setup.py` | `/v1/setup` wizard — the self-host door (Decision 6). |
| `sso.py` | **Basic OIDC SSO is free**, matching Langfuse. Enforced-SSO + SAML → enterprise. |
| `studio.py` | The wedge. |
| `verbs.py` | Agent-native eval-loop verbs — the moat, free. |
| `workspaces_me.py` | Workspace switcher. |

### Enterprise — 2 routers extracted + features not yet built

| Item | Source today | Enterprise shape |
|---|---|---|
| SCIM provisioning | `routers/scim.py` + `0020_scim.sql` | Plugin router mounted via entry point; gated on `scim` entitlement. |
| Audit retention/export/viewer | `routers/admin_audit.py` (reader) | Reader + retention policies + export move to plugin. **Capture stays core** (`_shared/tenant/audit.py`, `api/audit.py`) — security events must be recorded even on community; enterprise adds retention windows, export, and the viewer UI. |
| SAML / enforced-SSO | not built | New, enterprise-only. Attaches via the auth-provider registry. |
| Custom project-level RBAC | not built (single-org roles in core) | Policy layer over core's role checks; core exposes the check-point hook. |
| Data masking | not built | Ingest-pipeline plugin hook. |
| Protected prompt labels | not built | Gate inside `prompts.py` flows via `has_entitlement("protected_labels")`. |
| Data-retention policies | not built | Enterprise plugin drives core's (new, generic) retention executor. |

### Cloud — 2 routers extracted + everything billing

| Item | Source today | Cloud shape |
|---|---|---|
| Public OAuth signup | `routers/oauth_signup.py` + `0022_oauth_signup.sql` + `web/src/app/signup` | Moves to cloud repo. Self-host door stays `/v1/setup`; cloud door is OAuth signup → tenant provisioning. |
| Quota meters / admin bars | `routers/admin_quotas.py` | Moves to cloud with the metering implementation (#39). |
| Metering population, reconcilers | `_shared/tenant/quota.py` (impl), `0025_multitenancy_seam.sql` reconcilers | Cloud implements core's limiter interface (§2.3). |
| Stripe billing, rating engine, pricing config | not built | Cloud-only, per Decision 5. |
| Self-serve tenant provisioning, isolation hardening | partial (`0025` seam) | Cloud (#40). |
| Abuse/fraud, hosted-ops automation | not built | Cloud. |

### Shared modules (`services/_shared/tenant/langprobe_tenant/`)

| Module | Allocation |
|---|---|
| `context.py`, `resolver.py`, `shard.py` | **Core.** Org/workspace/project model + tenancy resolver stay OSS (#40). |
| `audit.py` | **Core** (capture). Enterprise consumes it for retention/export. |
| `quota.py` | **Split** (#39): interface stays core with an unlimited no-op default; Redis meter + reconciler implementation → cloud. |
| `rate_limit.py` | **Core.** Basic abuse protection is table stakes for self-host; cloud tightens per-tenant limits through the same interface. |
| `eval_concurrency.py` | **Core.** Eval loop substrate. |

### Services

All current services stay core: `api`, `ingest-api`, `ingest-worker`,
`migrator`, `operator`, `migrate-langsmith` (free and loud — the escape hatch
is customer acquisition), and the MCP surface (one of the two, per #46). The
cloud repo adds its own services (billing worker, provisioner) — never here.

### Web (`web/src/app/`) — mirrors the backend boundary (#45)

| Surface | Allocation |
|---|---|
| `runs`, `threads`, `evals`, `datasets`, `prompts`, `playground`, `studio`, `replay`, `comparisons`, `judges`, `poll-runs`, `alerts`, `annotations`, `monitoring`, `members`, `api-keys`, `workspace/credentials`, `login`, `feedback`, `privacy`, `terms` | **Core.** |
| `workspace/sso` | **Core** (basic OIDC config). SAML/enforced-SSO panels render only behind entitlements. |
| SCIM config, audit viewer, retention settings | **Enterprise** (don't exist yet / extracted with `admin_audit`). |
| `signup` | **Cloud** (public OAuth door). |
| Billing / usage / plan screens | **Cloud** (to be built there). |

Frontend gating: one `useEntitlements()` hook backed by a core endpoint
(`GET /v1/entitlements`, §2.2). Community build renders no dead paid-feature
chrome — gated routes/panels are simply absent, not disabled-greyed.

### Schema / migrations

- Core owns `schemas/postgres/migrations/` and `schemas/clickhouse/` — the
  shared schema, including tables that enterprise/cloud read (audit, quota
  period scaffolding from `0025`).
- Enterprise and cloud each run their **own migration stream** in their own
  repo for their own tables (billing, SAML config, retention policies),
  prefixed (`e0001_`, `c0001_`) and applied by the same migrator image after
  core's stream. Core never references their tables.
- Existing enterprise/cloud tables already in core migrations (`0020_scim`,
  `0022_oauth_signup`) stay — migrations are append-only history. New
  enterprise/cloud tables go in the new streams from day one.

## 2. Extension-point architecture (#37, #38)

Design principle: interfaces are OSS and generic; implementations are not.
Keep the surface minimal — exactly what SCIM/SAML/audit/RBAC/masking/metering
need, nothing speculative.

### 2.1 Plugin discovery

Python entry points, Grafana-style:

```toml
# langprobe-enterprise pyproject.toml
[project.entry-points."langprobe.plugins"]
enterprise = "langprobe_enterprise.plugin:EnterprisePlugin"
```

```python
class LangprobePlugin(Protocol):
    name: str

    def routers(self) -> list[APIRouter]: ...  # mounted after core routers
    def middleware(self) -> list[Middleware]: ...  # outermost-first
    def auth_providers(self) -> list[AuthProvider]: ...  # registered in the auth registry
    def ingest_hooks(self) -> list[IngestHook]: ...  # masking, usage emission
```

Core's app factory iterates discovered plugins at startup, logs what loaded.
No plugins installed → identical behavior to today. No config needed for the
common case; `LANGPROBE_PLUGINS_DISABLE=name` as the escape hatch.

### 2.2 Entitlements + license key (#37)

Core module `langprobe_api/entitlements.py`:

```python
def has_entitlement(feature: str) -> bool: ...  # community default: False
def edition() -> str: ...  # "community" | "enterprise" | "cloud"
```

- License key = signed token (Ed25519, public key baked into core),
  **offline-verifiable, no phone-home**. Claims: edition, entitlement set,
  org name, expiry.
- Loaded from `LANGPROBE_LICENSE_KEY` env / mounted file at boot. Invalid or
  expired → log loudly, run as community (graceful degradation, never a
  crash-loop).
- `GET /v1/entitlements` (authed) backs the frontend hook.
- Enterprise plugin refuses to activate its routers without a valid key
  (#43 boot check); cloud uses a cloud-tier key it issues itself.

### 2.3 Limiter / usage seam (#39)

```python
class UsageLimiter(Protocol):
    async def check(self, tenant_id: UUID, meter: str, n: int) -> Allow | Deny  # Deny → 429
    async def record(self, tenant_id: UUID, event: UsageEvent) -> None          # fire-and-forget
```

- Core default: `UnlimitedLimiter` — `check` always allows, `record` is a
  no-op. Self-host is unmetered, full stop.
- Instrumented **once** at the ingest-api / ingest-worker boundary (spans,
  events, scores) so nothing can bypass it.
- Cloud supplies the Redis hot-counter + postgres reconciler implementation
  (today's `quota.py` machinery moves there).

### 2.4 Auth-provider registry

Core registers OIDC (`sso.py`) and the setup wizard. Enterprise registers
SAML + enforced-SSO policy; cloud registers Google/GitHub OAuth signup. One
identity model (`app_user`, `workspace_membership`), multiple doors — no
second auth stack (Decision 6).

### 2.5 RBAC check-point hook

Core keeps `assert_org_role` and friends. Add one seam:
`authorize(principal, action, resource) -> bool` that defaults to today's
role check and can be wrapped by the enterprise project-RBAC policy engine.
No policy language in core.

## 3. Dependency rules + CI (#44)

- import-linter contract in core CI: `langprobe_api`, `langprobe_tenant`,
  ingest services **must not import** `langprobe_enterprise.*` or
  `langprobe_cloud.*`. (Nothing to catch until the repos exist — add the
  contract with the extension points so it's already failing-ready.)
- **Community build job**: install core alone, boot the API, run a smoke test
  (setup wizard, ingest one trace, read it back, `edition() == "community"`).
  This is the "core is genuinely complete" guarantee, run on every PR.
- Enterprise repo CI: install core from PyPI + itself, boot with a test
  license key, assert entitled routers mount; assert they refuse without a key.
- New-repo CI includes `ruff check` + `ruff format --check` from day one.

## 4. Distribution (#43)

Two consumption paths for enterprise:

1. **Private PyPI** — `pip install langprobe-enterprise` with license-gated
   index credentials; customer builds their own image on top of core's.
2. **Enterprise docker image** — prebuilt core+enterprise, published to a
   private registry; at boot, without a valid license key it runs as
   community (features simply absent — never a crash-loop on renewal lapse).

Enterprise repo license: source-available commercial (Elastic-2.0-style: view,
modify, no production use without a key). Core stays Apache-2.0 untouched.
Cloud repo: no license, never published.

## 5. Pricing tiers (extends locked Decision 5; numbers stay in config)

Prices are **placeholders for planning** — they live in the cloud repo's
rating config, never in code, and final numbers wait on the COGS validation
flagged in the 2026-07-02 doc (does ~3× under Langfuse clear ClickHouse
COGS?).

| Tier | Who | Meter | Placeholder | Includes |
|---|---|---|---|---|
| **Community** | self-host | none — unmetered | $0 | Everything Apache. Unlimited usage, unlimited seats. |
| **Cloud Free** | hosted | 100k units/mo, 30d retention | $0, no card | Full product, unlimited seats. |
| **Cloud Pay-as-you-go** | hosted | units (spans+events+scores) | ~$2.50/100k | No monthly floor. Retention as a unit multiplier (90d / 1yr+). Eval+replay compute as a metered add-on (the line item no competitor has). |
| **Cloud Team** | hosted | flat + included units | ~$99/mo | Extended retention bundled, standard support, usage overage at PAYG rate. |
| **Enterprise** | self-host **or** hosted | license (self-host) / annual contract (hosted) | sales-led | SCIM, SAML/enforced-SSO, audit retention/export, project RBAC, masking, retention policies, priority support, db-per-tenant isolation (hosted), SLA. |

Invariants: never per-seat; the wedge is never gated; self-host is never
metered; migrate-langsmith is free on every tier.

## 6. Support tiers

| Tier | Channel | Target |
|---|---|---|
| Community | GitHub issues + Discord | best-effort |
| Cloud Free/PAYG | email | best-effort, business hours |
| Team | email | first response 1 business day |
| Enterprise | dedicated channel (Slack Connect) | P1 4h / P2 1 business day (placeholder — final SLAs live in the order form, not code) |

Enterprise adds: upgrade assistance, LTS tags on the enterprise image
(security backports on a supported minor for 12 months), migration support
from LangSmith/Langfuse.

## 7. Compliance runway (cloud-facing; self-host inherits the customer's own)

Substrate already in the repo: audit capture (`audit.py` ×3), org-scoped
queries (`tenant_scope.py`, PRs #23/#30), immediate API-key revocation
(PR #30), SSO hardening (PR #20), PVC-backed stores (PR #52).

| Item | What | Where |
|---|---|---|
| KMS envelope encryption | `workspace_sso_config.client_secret_encrypted` is plaintext today — the column name is already the contract (`sso.py` docstring). Same for `llm_credentials` secrets. | **Core** (benefits self-hosters too). Only compliance item that is core code. |
| Data deletion / export | Per-tenant delete + export endpoints (GDPR art. 17/20). db-per-tenant strategy makes enterprise deletes clean. | Core endpoint shape; cloud wires retention + legal workflow. |
| SOC 2 Type I → II | Access reviews, change management, audit-trail retention, encryption at rest, backup/DR evidence, vendor review, pen test. | Cloud repo/ops. Target: Type I before first enterprise cloud deal; Type II ~6-9 months after. |
| DPA + subprocessor list | Standard DPA, list LLM providers used by cloud-side evals. | Cloud/legal. |
| EU data residency | Region-pinned cloud cell. | Cloud, later — deliberately not in v1. |

## 8. Migration sequence

```
#36 this doc
  ├─> #37 entitlements ─┬─> #41 enterprise repo ─┬─> #43 distribution
  ├─> #38 ext. points ──┤                        ├─> #44 CI boundary (contract lands with #38)
  │                     └─> #42 cloud repo ──────┤
  │                           ├─> #39 metering    └─> #45 web relocation
  │                           └─> #40 tenancy split
  └─> #46 MCP reconcile (independent — do first, after PRs #47–#51 land)
```

Phases, with rationale:

1. **#46 first** (independent, cheap): two MCP surfaces is churn every other
   issue would have to duplicate. Wait for the in-flight loop PRs (#47–#51)
   to merge, then converge — they touch `mcp-adapter`.
2. **#37 + #38 in parallel** (core-only, zero behavior change): entitlements
   returning community-false and empty plugin discovery ship dark. Import-
   linter contract (#44's first half) lands with #38.
3. **#41 then #42** (repos): enterprise first — it has real features to
   extract today (SCIM, audit reader); cloud scaffold follows since #39/#40
   need somewhere to move code *to*.
4. **#39 + #40** (the splits): move metering impl and provisioning/isolation
   to cloud; core keeps the unlimited no-op and the org model.
5. **#43 + #44 second half + #45**: distribution, community-build CI proof,
   web relocation last — UI follows the backend boundary once it's real.

**Sequencing vs the agent-loop pivot:** the loop track (#47–#51) continues
uninterrupted; nothing here touches loop code except #46, which explicitly
waits. The separation is seam work on ingest/auth/quota boundaries the loop
doesn't cross. Enterprise revenue work and moat work run in parallel lanes.

## 9. What this is NOT (guardrails)

- **No `/ee` folder in this repo.** The 2026-07-02 doc said `/ee`; epic #34
  superseded that with the plugin model — this doc follows #34.
- No speculative extension points: exactly the five seams in §2, added only
  when their first consumer lands.
- No feature flags sprinkled through core for paid features — gating happens
  in the plugin (router absent) or at one entitlement check-point, never
  inline `if enterprise:` branches.
- No phone-home, no telemetry requirement, no license server for self-host.
  Offline key verification only.
- If an enterprise feature can't be built behind the §2 seams without forking
  core internals, that's the GitLab failure mode — stop and redesign the seam
  before shipping the feature.

## 10. Acceptance criteria (for #36)

1. Every router in `services/api/langprobe_api/routers/` (34), every
   `_shared/tenant` module (7), every `web/src/app` surface, and every
   service has an explicit edition allocation in this doc — no "TBD".
2. Extension-point interfaces (§2) are specified concretely enough that #37
   and #38 need zero design decisions.
3. Dependency rule + CI enforcement defined (§3), including the community
   build proof.
4. Migration sequence orders all ten sibling issues with rationale, and
   states its relationship to the in-flight loop PRs (§8).
5. Pricing tier structure, support tiers, and compliance runway documented
   (§5–§7) with numbers as config placeholders, not code.
