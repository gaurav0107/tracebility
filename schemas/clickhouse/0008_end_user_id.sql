-- 0008_end_user_id.sql
-- End-user identity: distinguish the OPERATOR (run.user_id, who owns the
-- integration) from the END USER of the agent (end_user_id, the human the
-- agent is serving). LangSmith-parity feature so /threads can slice a
-- conversation by the actual person on the other side.
--
-- Follows the existing user_id/session_id pattern (0001): Nullable(String)
-- + a bloom_filter secondary index for per-end-user lookups without a
-- full scan. end_user_metadata is a JSON string column (default '') mirroring
-- how run.metadata is stored — free-form per-end-user attributes (plan, region,
-- cohort) the operator attaches at ingest.
--
-- Root-run-only concern: only the top-level run carries end-user identity.
-- Child spans inherit it via run_id, so there is no span table change.

alter table run add column if not exists end_user_id Nullable(String) after user_id;
alter table run add column if not exists end_user_metadata String default '' after end_user_id;
alter table run add index if not exists idx_run_end_user end_user_id type bloom_filter granularity 4;
