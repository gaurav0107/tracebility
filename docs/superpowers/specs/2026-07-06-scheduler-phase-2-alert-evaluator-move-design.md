# Scheduler Phase 2 — move the alert evaluator into the scheduler

> Date: 2026-07-06
> Status: approved for implementation
> Depends on: Phase 1 (PR #47 scheduler service + advisory locks, PR #48 watch_judge guard)
> Parent design: `2026-07-05-recurring-judge-scheduler-design.md` (this is its Phase 2)

## Why

The alert evaluator currently runs **in-process inside the API** as a lifespan task:
`services/api/langprobe_api/app.py:111` starts
`alerts.evaluator_loop(pg, clickhouse)`. The Helm chart runs the API at
`replicaCount > 1`, so **every replica runs the loop and evaluates every enabled
rule on its own cadence**. Two replicas that both see a rule breaching will both
open an incident → **duplicate `alert_event` rows / duplicate incidents**. This is
a live correctness bug in `main`, and a blocker for the SaaS posture.

Phase 2 moves the evaluator into the single-writer `scheduler` service introduced in
Phase 1 and makes the evaluation itself self-safe with a per-rule Postgres advisory
lock, so the bug is fixed regardless of how many scheduler replicas run.

**Scope:** pure correctness. No new alerting behavior, metrics, comparators, or
delivery channels. No schema change.

## What changes

### A. Extract a FastAPI-free evaluator module

Move the evaluation logic out of the router (`routers/alerts.py`, which imports
FastAPI) into a new FastAPI-free module so the lean scheduler can import it without
dragging in the web framework at runtime.

- **New** `services/api/langprobe_api/alerts/evaluator.py`, containing (moved
  verbatim from `routers/alerts.py`): `evaluate_due_rules`, `_measure` (and its
  ClickHouse SQL), `_apply_rule_decision`, `_compare`. Imports only `asyncpg`,
  `..clickhouse_client.ClickHouseQuery`, `datetime`, `uuid`, `structlog` — **no
  `fastapi` import**.
- `routers/alerts.py` imports from the new module whatever its route handlers still
  reference (e.g. `_measure`/`_compare` if used by preview endpoints) and **drops
  `evaluator_loop` entirely** (it exists only to serve the API lifespan, which is
  being deleted).
- Keep `evaluate_due_rules` importable at its new path. No test currently imports it
  (`grep` confirms zero references in `services/*/tests`), so there is no
  re-export compatibility burden.

**The double-fire fix lives inside `_apply_rule_decision`, not in the caller.**
`_apply_rule_decision` already opens one `conn.transaction()` per rule for its
writes. Add a per-rule advisory lock as the first statement of that transaction:

```sql
select pg_try_advisory_xact_lock(hashtextextended('alert-rule:' || $1::text, 0))
```

If the lock is not acquired, return early (another writer owns this rule this tick).
Because the lock is transaction-scoped and shares the decision's transaction, two
replicas can never both open an incident for the same rule; the lock auto-releases
on commit/connection-death. At `replicaCount: 1` it is a free no-op. The one-line
lock SQL is **inlined** in the evaluator (not shared with the scheduler's
`langprobe_scheduler/locks.py`) because api and scheduler are separate packages;
inlining keeps the evaluator self-safe for any caller and avoids a cross-package
dependency for one statement.

Measurement (`_measure`, a ClickHouse read) stays *before* the lock. If two replicas
both measure, only the lock winner writes the decision — the loser's read is wasted
but harmless.

### B. Scheduler alert tick

- **New** `services/scheduler/langprobe_scheduler/ticks/alerts.py`:
  `evaluate_alerts_once(pool, clickhouse)` → calls the extracted
  `evaluate_due_rules(pool, clickhouse)`. Thin wrapper for symmetry with
  `ticks/reaper.py` and to give the loop a stable seam to call/test.
- `langprobe_scheduler/app.py`: add an `alert_loop` beside `reaper_loop`, with the
  same `while True: try/except asyncio.CancelledError → re-raise / except Exception →
  log.warning; await asyncio.sleep(interval)` shape. `_serve` builds
  `ClickHouseQuery(settings.clickhouse_url)` once and passes it in; if
  `clickhouse_url` is unset the client is `None` and the tick no-ops (matching the
  evaluator's current `if clickhouse is None: return`). Start it as a second
  `asyncio.create_task`; cancel + await it in the same `finally` block that stops the
  reaper.
- `langprobe_scheduler/config.py`: add
  - `clickhouse_url: str | None` from `LANGPROBE_CLICKHOUSE_URL` (optional; unset ⇒
    alert tick no-ops),
  - `alert_interval_s: int = 60` from `LANGPROBE_SCHEDULER_ALERT_INTERVAL_S`.

### C. Remove the evaluator from the API lifespan

In `services/api/langprobe_api/app.py`:
- Delete the `evaluator_task = asyncio.create_task(alerts.evaluator_loop(...))` block
  (~line 111) and its `evaluator_task.cancel()` / `await evaluator_task` teardown
  (~lines 123–125).
- Remove the now-dead `evaluator_loop` function from `alerts.py`.

This deletion is the line that actually ends the double-fire in production; the
scheduler becomes the sole evaluator.

### D. Deploy wiring

- **Scheduler env** gains `LANGPROBE_CLICKHOUSE_URL` in all three surfaces, mirroring
  how the api wires clickhouse:
  - `infra/docker-compose.yml` scheduler service `environment:`,
  - `deploy/helm/langprobe/templates/scheduler-deployment.yaml` via
    `langprobe.envFromSecret` with `.Values.clickhouse` and name
    `LANGPROBE_CLICKHOUSE_URL`,
  - `services/operator/langprobe_operator/reconciler.py` `scheduler_envs`
    (`env_from_secret("LANGPROBE_CLICKHOUSE_URL", secrets.get("clickhouse"))`).
- **Packaging:** `services/scheduler/pyproject.toml` adds a dependency on
  `langprobe-api` (uv workspace member). `services/scheduler/Dockerfile` copies and
  installs `services/api` before `pip install .`, mirroring how
  `services/ingest-worker/Dockerfile` pre-installs `services/_shared/tenant` for a
  non-published workspace dependency.
- **Accepted tradeoff:** the scheduler image now installs the api package's
  dependencies (FastAPI, etc.). No FastAPI code executes on the tick path at runtime.
  Phase 3 (recurring-judge) reuses even more of the api package (`luna_judges`
  scoring, `llm/gateway`), so this dependency is paid once and amortized.

### E. Migration

None. Reuses `alert_rule` and `alert_event` unchanged. (The parent spec's mooted
`0032_alert_evaluator_lease.sql` is explicitly non-structural; the advisory lock is
runtime-only, no DDL.)

## Testing

- **Scheduler** `services/scheduler/tests/test_alert_tick.py` (integration, real
  Postgres via `LANGPROBE_TEST_DSN`, skip otherwise; a fake/minimal `ClickHouseQuery`
  returning a canned breaching value):
  - a rule whose measured value breaches its threshold opens exactly one `fired`
    `alert_event`;
  - **two `evaluate_alerts_once` calls run concurrently over the same due rule open
    exactly one incident** — the double-fire regression, asserted directly against
    the advisory lock;
  - a non-breaching rule with an open incident resolves it (port the existing
    resolve-path assertion).
- **API** `services/api/tests/unit/` stays green (no test imports the evaluator). Add
  a unit test asserting `_apply_rule_decision` performs no write when the per-rule
  advisory lock is already held by another connection.
- `ruff check` + `ruff format --check` clean.

## Out of scope (defer to Phase 3 and beyond)

- The recurring-judge tick, migration `0031`, `promote_to_recurring` wiring, and the
  `score_one` extraction — separate spec/plan/PR.
- Alert delivery channels (Slack/PagerDuty), new metrics/comparators, metering.
- A shared `_shared/alert-eval` library. Option A (extract-in-api + depend-on-api)
  was chosen over a `_shared` lib to avoid a large refactor; revisit only if the
  api-package dependency proves problematic.

## Merge order

Ships after Phase 1 (#47/#48). Independently shippable and independently revertible
(revert restores the API-lifespan evaluator). Phase 3 depends on the config/app-loop
seams this phase adds but not on its behavior.
