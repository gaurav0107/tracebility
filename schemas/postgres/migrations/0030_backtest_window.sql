-- 0030_backtest_window.sql
-- Persist the caller's window_hours on backtest_run so the executor
-- (_run_backtest) can re-derive the SAME cohort window that setup
-- (run_judge_over_cohort) sized and returned to the caller. Without
-- this, the executor's cohort selection could silently diverge from
-- the count the caller was shown.

begin;

alter table backtest_run add column if not exists window_hours int;

insert into schema_migrations (version) values ('0030_backtest_window')
on conflict (version) do nothing;

commit;
