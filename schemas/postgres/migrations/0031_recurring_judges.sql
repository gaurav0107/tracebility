-- 0031_recurring_judges.sql
-- Cadence for the recurring-judge scheduler tick (D2-A, "watch it").
--
-- promote_to_recurring inserts a plain luna_judge and stops; nothing
-- re-runs it against new traffic, so the self-improving loop is open.
-- These columns let the scheduler service (services/scheduler) find the
-- judges that are due, score only genuinely new runs since a watermark,
-- and record when it last ran / whether it errored.
--
--   is_recurring       — this judge participates in the recurring tick.
--   schedule_seconds   — cadence; NULL until promoted. Bounded 60..86400
--                        so a firehose can't schedule sub-minute or a
--                        typo can't schedule multi-day gaps unnoticed.
--   recurring_enabled  — soft pause without dropping the judge.
--   scored_through     — watermark: only runs with start_time > this are
--                        scored next tick. Advances to the newest run
--                        scored, so each tick touches only new traffic
--                        (idempotent + cheap; eval_score's
--                        ReplacingMergeTree collapses any retry overlap).
--   last_scored_at     — when the tick last ran this judge (drives "due").
--   last_score_error   — last tick's error text, if any (never silent).

begin;

alter table luna_judge
    add column if not exists is_recurring      boolean     not null default false,
    add column if not exists schedule_seconds  integer     check (schedule_seconds between 60 and 86400),
    add column if not exists recurring_enabled boolean     not null default true,
    add column if not exists scored_through     timestamptz,
    add column if not exists last_scored_at     timestamptz,
    add column if not exists last_score_error   text;

-- Partial index over exactly the rows the due-scan touches.
create index if not exists luna_judge_due_idx on luna_judge (project_id)
    where is_recurring and recurring_enabled and deleted_at is null;

insert into schema_migrations (version) values ('0031_recurring_judges')
on conflict (version) do nothing;

commit;
