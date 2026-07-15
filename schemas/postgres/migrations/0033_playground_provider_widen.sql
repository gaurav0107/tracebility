-- 0033_playground_provider_widen.sql
-- The playground model picker offers gemini / mistral / deepseek /
-- groq via the LiteLLM gateway, but playground_session's provider
-- check still only allowed anthropic | openai | stub — any other
-- provider 500'd on the insert. Align the constraint with the
-- gateway's SUPPORTED_PROVIDERS.

begin;

alter table playground_session
    drop constraint playground_session_provider_check;

alter table playground_session
    add constraint playground_session_provider_check
    check (provider in (
        'anthropic', 'openai', 'gemini', 'mistral',
        'deepseek', 'groq', 'stub'
    ));

insert into schema_migrations (version) values ('0033_playground_provider_widen')
on conflict (version) do nothing;

commit;
