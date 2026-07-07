-- 0033_recurring_judge_run_cursor.sql
-- Tie-break the recurring-judge watermark on run_id.
--
-- 0031 gave luna_judge a single `scored_through` timestamp watermark and
-- the tick reads runs with `start_time > scored_through`. That drops runs
-- on a start_time tie at the max_cohort boundary: if the Nth (cap) and
-- (N+1)th runs share an identical start_time, the tick scores the Nth,
-- advances the watermark to that timestamp, and the strict `>` then skips
-- the (N+1)th forever. Ties are realistic — many tracing SDKs stamp
-- start_time at millisecond (or second) granularity, so a busy project
-- routinely lands several runs in the same instant.
--
-- Fix: make the watermark a composite cursor (start_time, run_id) and
-- paginate with `(start_time, run_id) > (scored_through, scored_through_run_id)`
-- ordered by the same tuple. No run is ever stranded, and a timestamp with
-- more runs than max_cohort simply drains across successive ticks.
--
--   scored_through_run_id — run_id half of the cursor. NULL means "start of
--                           stream" (freshly promoted judge); the tick
--                           coalesces it to the zero UUID, the minimum in
--                           UUID order, so the first tick scores forward
--                           from `scored_through` inclusive of every run_id.

begin;

alter table luna_judge
    add column if not exists scored_through_run_id uuid;

insert into schema_migrations (version) values ('0033_recurring_judge_run_cursor')
on conflict (version) do nothing;

commit;
