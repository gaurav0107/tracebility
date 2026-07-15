-- 0031_playground_session_raw_messages.sql
-- The Plan-B structured playground path (raw_messages) leaves both
-- prompt_version_id and raw_template null, which violates
-- playground_session_source_present and 500s every structured
-- playground run. Persist the structured source in its own column and
-- teach the constraint about it, so a session always records where its
-- prompt came from (version ref, legacy string, or structured messages).

begin;

alter table playground_session
    add column raw_messages jsonb;

alter table playground_session
    drop constraint playground_session_source_present;

alter table playground_session
    add constraint playground_session_source_present
    check (
        prompt_version_id is not null
        or raw_template is not null
        or raw_messages is not null
    );

insert into schema_migrations (version) values ('0031_playground_session_raw_messages')
on conflict (version) do nothing;

commit;
