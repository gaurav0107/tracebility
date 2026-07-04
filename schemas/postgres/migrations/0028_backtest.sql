-- 0028_backtest.sql
-- Persistence for the agent-native eval-loop's draft/backtest lifecycle
-- (D2-A). An AI agent clusters failing traces, proposes a judge
-- (backtest_draft), backtests it against history (backtest_run), and
-- — if the operator likes the results — promotes it to a recurring
-- eval_config. This migration only creates the scratch tables; the
-- promotion path and the ClickHouse SCRATCH score store are separate.

begin;

create table if not exists backtest_draft (
    id              uuid primary key default gen_random_uuid(),
    project_id      uuid not null,
    org_id          uuid not null,
    cluster_ref     jsonb not null,
    judge_kind      text not null,
    judge_config    jsonb not null,
    status          text not null default 'drafting'
        check (status in ('drafting', 'backtesting', 'ready', 'promoted', 'discarded')),
    created_by      uuid,
    created_at      timestamptz not null default now(),
    heartbeat_at    timestamptz,
    error           text
);

create index if not exists backtest_draft_project_idx
    on backtest_draft (project_id);
create index if not exists backtest_draft_status_idx
    on backtest_draft (status);

create table if not exists backtest_run (
    id                      uuid primary key default gen_random_uuid(),
    draft_id                uuid not null references backtest_draft (id) on delete cascade,
    status                  text not null default 'queued'
        check (status in ('queued', 'running', 'done', 'failed')),
    cohort_size             int,
    spans_scanned           int,
    cost_usd                numeric,
    caught                  int,
    missed                  int,
    would_have_flagged_at   timestamptz,
    item_total              int,
    item_done               int,
    heartbeat_at            timestamptz,
    started_at              timestamptz,
    finished_at             timestamptz,
    error                   text
);

create index if not exists backtest_run_draft_idx
    on backtest_run (draft_id);
create index if not exists backtest_run_status_idx
    on backtest_run (status);

insert into schema_migrations (version) values ('0028_backtest')
on conflict (version) do nothing;

commit;
