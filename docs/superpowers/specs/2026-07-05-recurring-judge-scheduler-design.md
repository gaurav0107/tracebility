# Recurring-judge scheduler — design

> Date: 2026-07-05
> Status: draft for review
> Author: feature-opportunity analysis (kp)
> Depends on: D2-A agent-native eval loop (verbs `cluster_failures → propose_eval →
> run_judge_over_cohort → promote_to_recurring → watch_judge`, PRs #30–#33)

## Why (the gap this closes)

The product wedge is a **self-improving loop**: an agent clusters failing traces,
proposes a judge, backtests it, "**registers it as a recurring judge → and watches
it**" (README; roadmap spec `2026-07-02-oss-and-cloud-versions-design.md` §2). The
first four verbs are real. The fifth is not:

1. **Nothing makes a promoted judge recur.** `promote_to_recurring`
   (`verbs/promote.py`) inserts a `luna_judge` row and stops. There is no cadence
   column on `luna_judge` (`schemas/postgres/migrations/0018_luna_judges.sql`) and
   no process that re-runs it against new traffic. The judge exists but never fires
   again. The loop is open.

2. **`watch_judge` only polls one backtest; it is not "watching" production.** Its
   docstring is explicit: *"Rather than requiring a separate durable reaper process
   for this MVP slice, watch_judge itself declares the run dead on the next poll."*
   Stale `backtest_run` rows are reaped in-band, only if someone happens to poll.
   Orphaned runs from a pod OOM sit at `running` forever otherwise.

3. **The one existing periodic evaluator is single-replica-unsafe.**
   `alerts.evaluator_loop` (`routers/alerts.py:377`) is a `while True: evaluate;
   sleep(60)` task started inside the **API** process lifespan (`app.py:111`). The
   Helm chart runs the API at `replicaCount > 1`, so **every replica runs the loop
   and double-evaluates every rule** — duplicate `alert_event` rows, duplicate
   incidents. This is a latent bug today and a correctness blocker for the SaaS
   posture. The scheduler must not copy this shape.

**This spec introduces one durable, single-writer scheduler service** that owns all
three periodic responsibilities: run recurring judges over new traffic, reap stale
backtests, and (by migrating it off the API) evaluate alert rules safely.

## What it does

A new service `services/scheduler` (mirrors `services/ingest-worker` in shape,
packaging, Dockerfile, config, and lifecycle) that runs three ticks on independent
intervals:

| Tick | Interval (default) | Responsibility |
|---|---|---|
| **recurring-judge** | 300s | Score new runs against each due, enabled recurring judge; write `eval_score`; feed results to alerts. Closes the loop. |
| **backtest-reaper** | 60s | Flip `backtest_run` rows stuck at `running` past the lease timeout to `failed` (durably, not in-band). Removes the `watch_judge` hack's reason to exist. |
| **alert-evaluator** | 60s | The *existing* `alerts.evaluate_due_rules` logic, moved here verbatim and run in one place. Deleted from the API lifespan. |

Non-goals: delivering alert routes (Slack/PagerDuty — still out of scope per
`0011_alerts.sql`), the `refine_eval` iteration verb (separate spec), metering.

## Architecture

### Dedicated worker, not an in-process task

Follow the `ingest-worker` precedent, not the `alerts.evaluator_loop` precedent:

- **A separate deployment** means the scheduler scales, restarts, and fails
  independently of request-serving. A judge scoring loop that scans ClickHouse must
  never contend with API request latency.
- **Single logical writer.** The scheduler is deployed at `replicaCount: 1` by
  default. For HA (`replicaCount: 2`), correctness is guaranteed by **per-work-item
  Postgres advisory locks** (below), not by hoping only one replica exists.

```
services/scheduler/
  Dockerfile
  pyproject.toml
  langprobe_scheduler/
    __init__.py
    __main__.py
    app.py          # lifespan: build deps, start the three ticks, SIGTERM-clean
    config.py       # env → Settings (mirror ingest-worker/config.py)
    ticks/
      recurring.py  # recurring-judge tick
      reaper.py     # backtest-reaper tick
      alerts.py     # thin shim → import alerts.evaluate_due_rules from the api pkg
    locks.py        # pg_try_advisory_lock helpers
  tests/
    test_recurring_watermark.py
    test_reaper_lease.py
    test_advisory_lock.py
```

### Multi-replica safety: Postgres advisory locks

Every unit of periodic work acquires a **transaction-scoped advisory lock** keyed by
a hash of `(tick_name, work_item_id)` before doing anything, and skips if the lock is
held:

```sql
select pg_try_advisory_xact_lock(hashtextextended('recurring-judge:' || $1, 0));
```

- Recurring-judge tick locks **per judge id** — two replicas can score *different*
  judges concurrently, never the same one.
- Alert-evaluator locks **per rule id** — fixes the existing double-fire bug as a
  side effect of the move.
- Reaper locks **per backtest_run id**.

No new infra (Redis lock, Zookeeper) — Postgres is already the control plane. Locks
auto-release on transaction end or connection death, so a crashed replica never
wedges a work item.

## Data model changes

### Migration `0031_recurring_judges.sql` — cadence on `luna_judge`

`promote_to_recurring` currently produces a plain judge. Add the scheduling columns
it should stamp:

```sql
alter table luna_judge
    add column is_recurring      boolean     not null default false,
    add column schedule_seconds  integer     check (schedule_seconds between 60 and 86400),
    add column recurring_enabled boolean     not null default true,
    -- watermark: only score runs with start_time > this. Advances each tick.
    add column scored_through     timestamptz,
    add column last_scored_at     timestamptz,
    add column last_score_error   text;

create index luna_judge_due_idx on luna_judge (project_id)
    where is_recurring and recurring_enabled and deleted_at is null;
```

`promote_to_recurring` (`verbs/promote.py`) gains: set `is_recurring = true`,
`schedule_seconds = params.schedule_seconds` (default 3600), `scored_through =
now()` (so a freshly promoted judge scores *forward*, not over all history — the
backtest already covered history).

### Migration `0032_alert_evaluator_lease.sql` — nothing structural

The alert move reuses `alert_rule`/`alert_event` unchanged; only the *evaluator's
home* changes.

No ClickHouse migration: recurring judges write to the existing `eval_score` table
(same store `routers/evals.py` and `luna_judges` already use), keeping the analytic
shape uniform per `0018`'s stated design.

## The recurring-judge tick (the core)

Pseudocode for one tick. **Reuses existing scoring** — does not reinvent the judge
dispatch:

```python
async def recurring_tick(deps):
    due = await deps.pg.fetch("""
        select id, project_id, slug, scored_through
          from luna_judge
         where is_recurring and recurring_enabled and deleted_at is null
           and (last_scored_at is null
                or last_scored_at < now() - make_interval(secs => schedule_seconds))
    """)
    for judge in due:
        async with advisory_lock(deps.pg, "recurring-judge", judge["id"]) as got:
            if not got:
                continue  # another replica owns this judge this tick
            watermark = judge["scored_through"] or default_lookback()
            # bounded: newest N runs since the watermark, capped like backtest MAX_COHORT
            runs = await select_new_runs(deps.ch, judge["project_id"], watermark, cap=MAX_COHORT)
            if not runs:
                await bump_last_scored_at(deps.pg, judge["id"])
                continue
            judge_cfg = await luna_judges.resolve_judge(deps.pg, judge["project_id"], judge["slug"])
            for run in runs:
                score = await luna_judges.score_one(deps.llm, judge_cfg, run)  # SHARED path
                await write_eval_score(deps.ch, judge, run, score)             # SHARED store
            new_watermark = max(r.start_time for r in runs)
            await advance_watermark(deps.pg, judge["id"], new_watermark)
            await maybe_open_alert(deps.pg, judge, aggregate(runs_scores))     # reuse alert_event
```

Design points:

- **Watermark, not re-scan.** `scored_through` advances to the newest run scored, so
  each tick only scores genuinely new traffic — idempotent and cheap. `eval_score`'s
  ReplacingMergeTree collapses any overlap on retry (same guarantee the ingest
  worker relies on).
- **Bounded work.** Cohort per tick is clamped to `MAX_COHORT` (reuse the constant
  from `verbs/backtest.py`) and a per-tick cost cap, so a firehose project can't make
  one tick unbounded. Backpressure = the watermark simply lags; next tick catches up.
- **Shared scoring seam.** `luna_judges` today has the resolve + parse logic inline in
  the eval router. Extract `score_one(llm, judge_cfg, item) -> Score` into
  `routers/luna_judges.py` (or a `langprobe_api/eval/scoring.py` module) and call it
  from *both* `routers/evals.py` and the scheduler. This is the one refactor the
  spec requires; it prevents judge-behavior drift between manual eval-runs and
  recurring runs (the exact "never drift between the two surfaces" principle the verb
  registry already follows).
- **Feeds alerts for free.** Because scores land in `eval_score`, a recurring judge
  whose average crosses a threshold can open an incident through the *same*
  `alert_event` machinery — the loop's "watch it" step becomes a real alert, not a
  dashboard someone has to look at.

## Reuse map (what NOT to build)

| Need | Existing thing to reuse | Location |
|---|---|---|
| Worker skeleton, SIGTERM, structlog | ingest-worker `app.py`/`config.py` | `services/ingest-worker/` |
| Judge resolve + rubric dispatch + parse | `luna_judges` (extract `score_one`) | `routers/luna_judges.py`, `routers/evals.py` |
| Score store + ReplacingMergeTree idempotency | `eval_score` ClickHouse table | `schemas/clickhouse/0002_eval_scores.sql` |
| Alert firing / incident open+resolve | `alerts.evaluate_due_rules` + `alert_event` | `routers/alerts.py` |
| Lease/stale detection semantics | `watch_judge` LEASE_TIMEOUT_S logic | `verbs/watch.py` |
| Tenant scoping (no unscoped queries) | `require_project_scope`, TenantContext | `verbs/scope.py`, `_shared/tenant` |
| LLM dispatch + provider routing | `llm/gateway.py` | `services/api/langprobe_api/llm/` |

## Config (env, mirroring ingest-worker)

```
LANGPROBE_PG_URL                     (required)
LANGPROBE_CLICKHOUSE_URL             (required)
LANGPROBE_SCHEDULER_RECURRING_INTERVAL_S   default 300
LANGPROBE_SCHEDULER_REAPER_INTERVAL_S      default 60
LANGPROBE_SCHEDULER_ALERT_INTERVAL_S       default 60
LANGPROBE_SCHEDULER_LEASE_TIMEOUT_S        default 300  (match watch.py)
LANGPROBE_SCHEDULER_MAX_COHORT             default = verbs/backtest MAX_COHORT
LANGPROBE_SCHEDULER_TICK_COST_CAP_USD      default 1.00
LANGPROBE_LOG_LEVEL                        default INFO
```

## Deploy

- **Helm:** new `deploy/helm/langprobe/templates/scheduler-deployment.yaml` (copy
  `ingest-worker-deployment.yaml`), `replicaCount: 1` default, values under
  `scheduler:` in `values.yaml`. No new service/ingress (no inbound traffic).
- **Operator:** add `scheduler` to the managed workload list in
  `services/operator/langprobe_operator/reconciler.py`.
- **docker-compose:** add a `scheduler` service to `infra/docker-compose.yml` so
  self-host gets the closed loop out of the box.
- **Remove** the `evaluator_task = asyncio.create_task(alerts.evaluator_loop(...))`
  block from `services/api/langprobe_api/app.py` lifespan once the scheduler owns it.
  Keep `evaluate_due_rules` importable (it already is) so the scheduler shim calls it.

## Testing

Mirror the existing worker/verb test style (`services/ingest-worker/tests`,
`services/api/tests`):

- `test_recurring_watermark.py` — watermark advances; a second tick with no new runs
  is a no-op; overlapping runs don't double-count after the ReplacingMergeTree merge.
- `test_reaper_lease.py` — a `running` backtest with a stale heartbeat is flipped to
  `failed`; a fresh-heartbeat one is left alone (property already asserted by
  `verbs/watch.py` — port the assertion).
- `test_advisory_lock.py` — two concurrent tick invocations over the same judge id
  score it exactly once (the double-fire regression, asserted directly).
- Integration: promote a draft → assert a `luna_judge` row with `is_recurring=true`
  and `scored_through≈now()` → ingest a new run → run one tick → assert exactly one
  `eval_score` row for that (judge, run).
- **CI:** add a `pytest` job to `.github/workflows/ci.yml` — it currently runs only
  `ruff`/`typecheck` and never executes the 62 test files, so none of the above would
  actually gate a merge without it. (Separate finding; the scheduler needs it to be
  safe to ship.)

## Phasing

1. **Reaper first** (smallest, highest safety payoff): dedicated service + the
   backtest-reaper tick + advisory locks. Delete the in-band reap rationale from
   `watch_judge`. Ships the service skeleton.
2. **Move the alert evaluator** into it; remove from API lifespan. Fixes the
   double-fire bug. No new behavior, pure correctness.
3. **Recurring-judge tick** + migration `0031` + `promote_to_recurring` wiring +
   `score_one` extraction. Closes the loop.

Each phase is independently shippable and testable.

## Open questions

1. **Watermark backfill on promote:** score forward-only (proposed: `scored_through
   = now()`) vs. also sweep the gap between backtest end and promotion. Forward-only
   is simpler and the backtest already covered history — recommend forward-only,
   revisit if users report missed windows.
2. **Per-judge vs. global cost ceiling** for recurring scoring — ties into the
   deferred metering subsystem (`2026-07-02` spec Decision 5). For now a flat
   per-tick cap; make it a metered add-on later ("eval + replay compute" is already
   named as a billable line item there).
3. **HA default:** ship `replicaCount: 1` (locks make 2 safe but 1 is simplest) or
   default to 2 to prove the lock path in the field? Recommend 1 default, document 2.
