# Recurring-judge alert feed + per-tick cost cap — design

> Date: 2026-07-07
> Status: approved for implementation
> Depends on: Phase 3 (PR #51 recurring-judge tick, migration `0031`, `promote_to_recurring` cadence wiring)
> Parent design: `2026-07-05-recurring-judge-scheduler-design.md` (this completes its Phase 3)

## Why

Phase 3 (PR #51) made judges recur and write `eval_score` rows, but two design
promises from the parent spec did not actually ship:

1. **The loop does not close into alerts.** The parent spec's core payoff is that
   "a recurring judge whose average crosses a threshold opens an incident through
   the same `alert_event` machinery" (§"The recurring-judge tick", pseudocode line
   `maybe_open_alert(...)`). In the shipped code nothing watches the scores:
   `alerts/evaluator.py:_measure` only supports `error_rate`, `latency_p95_ms`,
   `runs_per_min`, `cost_usd`, all read from the `run` table. No metric reads
   `eval_score`, and the recurring tick has no incident-open step. Judges recur and
   write scores that nothing watches — the "watch it" step of the self-improving
   loop is not wired.

2. **No per-tick cost cap.** The parent spec's Config lists
   `LANGPROBE_SCHEDULER_TICK_COST_CAP_USD` (default 1.00) and the design calls for
   "a per-tick cost cap, so a firehose project can't make one tick unbounded."
   Only `max_cohort` (a count) bounds work today. A judge with 500 new runs scores
   all 500 LLM calls with no dollar ceiling.

This spec closes both, inside PR #51, without new incident-writing code and without
new infra.

**Scope:** finish Phase 3. No new alert delivery channels, no new comparators, no
metering subsystem.

## Key constraint that picks the approach

`alert_event.rule_id` is `NOT NULL REFERENCES alert_rule(id)`
(`schemas/postgres/migrations/0011_alerts.sql`). An incident cannot exist without a
rule. So the parent spec's `maybe_open_alert()`-in-the-tick sketch cannot write an
`alert_event` without first inventing a rule — which means the honest way to "open
an incident through the same `alert_event` machinery" is to **give the existing
alert-evaluator an `eval_score`-backed metric** and let a rule watch the judge. The
recurring tick stays a pure scorer; the evaluator — which already runs in the
scheduler with per-rule advisory locks, open/resolve, and `alert_event` history —
does the watching. This is maximal reuse and zero divergence.

## What changes

### A. Migration `0032_judge_score_alerts.sql`

```sql
begin;

-- Extend the metric whitelist with an eval_score-backed metric.
alter table alert_rule drop constraint alert_rule_metric_check;
alter table alert_rule add constraint alert_rule_metric_check check (
    metric in ('error_rate', 'latency_p95_ms', 'runs_per_min', 'cost_usd', 'judge_score_avg')
);

-- The luna_judge a judge-scoped rule watches. NULL for the four run-based
-- metrics; required for judge_score_avg (enforced below).
alter table alert_rule add column if not exists subject_id uuid;

alter table alert_rule add constraint alert_rule_subject_check check (
    (metric = 'judge_score_avg') = (subject_id is not null)
);

-- One watch rule per judge per metric — makes promote's auto-provision
-- idempotent (INSERT ... ON CONFLICT DO NOTHING) and stops duplicate rules.
create unique index if not exists alert_rule_judge_watch_uniq
    on alert_rule (subject_id, metric) where subject_id is not null;

insert into schema_migrations (version) values ('0032_judge_score_alerts')
on conflict (version) do nothing;

commit;
```

Note the `(metric = 'judge_score_avg') = (subject_id is not null)` biconditional: a
judge metric MUST carry a subject, and a run metric must NOT. This keeps the two
rule shapes from being mixed up.

### B. Evaluator — `judge_score_avg` branch (`alerts/evaluator.py`)

- Add `subject_id` to the `evaluate_due_rules` rule SELECT.
- New `_measure` branch:

```sql
select avg(score) as avg_score
  from eval_score final
 where project_id = {project_id:UUID}
   and eval_config_id = {subject_id:UUID}
   and judged_at >= now64(9) - toIntervalSecond({window:UInt32})
```

- Returns `None` (⇒ no fire) when the window holds no scores. `FINAL` matches the
  `run final` precedent and collapses ReplacingMergeTree overlap so the average
  isn't skewed by unmerged retry rows.
- Because recurring scores are written with `eval_config_id = judge_id` (the
  recurring tick's dedup key), filtering on `eval_config_id = subject_id` watches
  **recurring** scores specifically and never mixes in manual-eval rows (those use
  `eval_config_id = eval_config.id`).

Everything downstream — the per-rule advisory lock, open/resolve, `alert_event`
writes, `open_incident_id` bookkeeping, UI history — is unchanged.

### C. Router — accept the judge metric (`routers/alerts.py`)

- Add `'judge_score_avg'` to `_METRICS`.
- Create + update paths accept an optional `subject_id`; validate the biconditional
  (present iff metric is `judge_score_avg`) and reject otherwise with 400. Persist
  `subject_id`. Echo it in the rule response model.
- This lets the UI create judge-score rules and lets `promote_to_recurring` create
  one through the same insert shape.

### D. `promote_to_recurring` auto-provisions the watch rule (`verbs/promote.py`)

After inserting/looking up the judge, insert a default watch rule:

```sql
insert into alert_rule (
    project_id, name, metric, comparator, threshold,
    window_seconds, subject_id, enabled, created_by
)
values ($1, $2, 'judge_score_avg', '<', 0.5, $3, $4, true, $5)
on conflict (subject_id, metric) where subject_id is not null do nothing
```

- `comparator = '<'`, `threshold = 0.5`: luna scores are higher-is-better, so the
  rule fires when the judge's windowed average quality drops below 0.5 (a
  regression). Users can edit or disable it.
- `window_seconds = clamp(schedule_seconds, 60, 86400)`: align the alert window to
  the judge's scoring cadence, within the column's bound.
- `enabled = true` is safe: alert **delivery** is still out of scope, so a fired
  incident only surfaces in the UI — non-intrusive by construction.
- `ON CONFLICT ... DO NOTHING` on the partial unique index makes this idempotent,
  matching the verb's existing idempotency contract (a promote retry converges on
  the same judge *and* the same rule, never a duplicate).

This is the line that closes the loop with zero extra user action — the parent
spec's "register it as recurring → and watch it".

### E. Per-tick cost cap (`ticks/recurring.py`, `config.py`, and the scoring seam)

**Seam returns cost.** `apply_luna_judge` (`routers/luna_judges.py`) currently drops
`DispatchResult.cost_usd`. Extend its return to a 5-tuple
`(score, label, rationale, raw_output, cost_usd)`:

- Gateway path: `cost_usd = result.cost_usd or 0.0`.
- Stub path and `DispatchError` path: `cost_usd = 0.0`.
- Update its three callers to unpack the new field:
  `routers/evals.py`, `verbs/backtest.py`, `ticks/recurring.py`.
- Bonus fix: the recurring tick now writes the **real** `eval_score.cost_usd`
  instead of the hardcoded `0`.

**Config.** Add to `Settings`:
`recurring_tick_cost_cap_usd: float = 1.00`, from
`LANGPROBE_SCHEDULER_TICK_COST_CAP_USD`. A value `<= 0` means unlimited (cap off).

**Budget accumulator.** A small object spans the whole tick (all judges), so the
cap is per-tick as specified:

```python
class _TickBudget:
    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self.spent = 0.0
    def charge(self, usd: float) -> None:
        self.spent += usd
    def exhausted(self) -> bool:
        return self.cap > 0 and self.spent >= self.cap
```

- `evaluate_recurring_once` builds one `_TickBudget` and passes it to `_score_judge`.
- Inside the run loop: score the run, `budget.charge(cost)`, then check
  `budget.exhausted()`. If exhausted, stop scoring further runs.
- Because runs are scored oldest-first, the runs scored are always a clean prefix,
  so `new_watermark` = the last scored run's `start_time` and the next tick resumes
  exactly there. Backpressure = the watermark lags; nothing is lost.
- After a judge finishes (or is cut short), `evaluate_recurring_once` checks
  `budget.exhausted()` and breaks the outer due-loop too — the tick ends, next tick
  continues from each judge's watermark.
- The cap is **coarse/soft**: it is checked *after* each score, so a tick can
  overshoot by at most one score's cost. This is intentional and documented — the
  goal is bounding a firehose, not exact accounting.

## Reuse map (what NOT to build)

| Need | Reuse | Location |
|---|---|---|
| Incident open/resolve, dedup, per-rule lock, history | `evaluate_due_rules` + `alert_event` | `alerts/evaluator.py`, `0011_alerts.sql` |
| Per-call LLM cost | `DispatchResult.cost_usd` | `llm/types.py`, `llm/gateway.py` |
| Judge scoring (no drift) | `apply_luna_judge` (already the shared seam) | `routers/luna_judges.py` |
| Rule create/validate/persist | existing alerts router insert path | `routers/alerts.py` |

## Testing

- **`services/scheduler/tests/test_recurring_watermark.py`** — cost cap: a judge with
  N new runs, injected `_apply` returning a fixed per-score cost; cap set so only a
  prefix is affordable → exactly that prefix is scored, `scored_through` advances to
  the last scored run, and a second tick resumes and finishes the rest.
- **`services/api/tests/unit/`** (evaluator) — `judge_score_avg`: a fake
  `ClickHouseQuery` returning a canned `avg_score` that breaches ⇒ exactly one
  `fired` `alert_event`; a non-breaching value with an open incident ⇒ resolved.
  Assert `subject_id` flows into the measurement query.
- **promote test** (`test_verb_loop_e2e.py` or a unit) — promoting a ready draft
  creates the judge AND a bound `judge_score_avg` `alert_rule`
  (`subject_id = judge_id`, `comparator='<'`, `threshold=0.5`); a second promote of
  the same config does not create a second rule.
- **router test** — creating a rule with `metric='judge_score_avg'` and no
  `subject_id` is rejected 400; a run metric with a `subject_id` is rejected 400.
- `ruff check` + `ruff format --check` clean.

## Out of scope

- Alert delivery channels (Slack/PagerDuty/webhook/email) — still v1-deferred per
  `0011_alerts.sql`.
- New comparators or aggregation methods (median/p95 of scores) — `avg` only.
- The strict-`>` watermark boundary edge (runs sharing an identical `start_time` at
  a cohort-cap boundary) — a separate, pre-existing finding; not touched here.
- Metering / per-judge billing ceilings — deferred per the parent spec's Open
  Question 2.

## Merge

Lands inside PR #51 (Phase 3), which the loop-closure and cost-cap complete. After
this, the branch's "closes the self-improving loop" claim is accurate:
promote → recur → score → watch → incident.
