-- 0032_judge_score_alerts.sql
-- Close the recurring-judge loop into alerts: let an alert_rule watch a
-- recurring judge's windowed average eval_score. See
-- docs/superpowers/specs/2026-07-07-recurring-judge-alert-feed-and-cost-cap-design.md
--
--   judge_score_avg  — new metric; value = avg(eval_score.score) over the
--                      window for the bound judge (eval_config_id = subject_id).
--   subject_id       — the luna_judge a judge-scoped rule watches. NULL for
--                      the four run-based metrics; required for judge_score_avg.

begin;

-- Extend the metric whitelist.
alter table alert_rule drop constraint alert_rule_metric_check;
alter table alert_rule add constraint alert_rule_metric_check check (
    metric in ('error_rate', 'latency_p95_ms', 'runs_per_min', 'cost_usd', 'judge_score_avg')
);

-- Judge binding: present iff the metric is judge-scoped.
alter table alert_rule add column if not exists subject_id uuid;
alter table alert_rule add constraint alert_rule_subject_check check (
    (metric = 'judge_score_avg') = (subject_id is not null)
);

-- One watch rule per judge per metric — makes promote's auto-provision
-- idempotent and stops duplicate rules.
create unique index if not exists alert_rule_judge_watch_uniq
    on alert_rule (subject_id, metric) where subject_id is not null;

insert into schema_migrations (version) values ('0032_judge_score_alerts')
on conflict (version) do nothing;

commit;
