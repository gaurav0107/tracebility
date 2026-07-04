-- 0008_backtest_score.sql
-- SCRATCH judge outputs for backtest runs (D2-A). Mirrors eval_score
-- exactly, keyed by draft_id instead of eval_config_id. This is the
-- scratch store used while an AI-proposed judge is being backtested;
-- once a draft is promoted, the real judge writes to eval_score
-- instead and these rows age out via the short TTL below.

create table if not exists backtest_score
(
    project_id        UUID,
    run_id            UUID,
    span_id           Nullable(UUID),
    -- references postgres backtest_draft.id
    draft_id          UUID,
    -- which judge in the panel produced this row
    judge_name        LowCardinality(String),
    judge_endpoint    LowCardinality(String),
    judge_version     LowCardinality(String),
    -- numeric score; rubric-defined range, app-side normalization
    score             Float64,
    -- the categorical label, if any (e.g. 'pass' | 'fail' | 'partial')
    label             LowCardinality(String) default '',
    -- judge rationale; can be long
    rationale         String,
    -- raw judge output (pre-parse), kept for replay/audit
    raw_output        String,
    -- 'ok' | 'judge_unavailable' | 'schema_violation' | 'rate_limited' | 'cost_ceiling'
    -- per ER-12, ER-13, ER-14
    outcome           LowCardinality(String) default 'ok',
    -- bookkeeping
    judged_at         DateTime64(9, 'UTC') default now64(9),
    cost_usd          Decimal(18, 8) default 0,
    schema_version    UInt8 default 1
)
engine = ReplacingMergeTree(judged_at)
partition by toYYYYMM(judged_at)
order by (project_id, draft_id, run_id, judge_name)
ttl toDateTime(judged_at) + interval 7 day
settings index_granularity = 8192;
