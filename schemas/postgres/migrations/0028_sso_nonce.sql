-- 0028_sso_nonce.sql
-- Add an OIDC `nonce` to the SSO round-trip state.
--
-- The nonce is generated at /start, sent to the IdP in the
-- authorization request, and echoed back in the id_token. At
-- /callback we verify the id_token's `nonce` claim matches the value
-- we stored, which binds the returned token to this specific sign-in
-- attempt and blocks id_token replay/injection.
--
-- Nullable so in-flight sso_state rows created before this migration
-- still validate (nonce is only enforced when present). New rows
-- always populate it.

begin;

alter table sso_state add column if not exists nonce text;

insert into schema_migrations (version) values ('0028_sso_nonce')
on conflict (version) do nothing;

commit;
