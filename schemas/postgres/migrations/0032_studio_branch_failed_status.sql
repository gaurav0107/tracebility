-- 0032_studio_branch_failed_status.sql
-- A branch whose replay dispatch failed used to flip to 'replayed'
-- anyway — the success badge lied and the edit list froze. Give the
-- lifecycle an explicit 'failed' state (draft -> replayed|failed ->
-- promoted) so the UI can show a danger badge and keep the canvas
-- editable for a retry.

begin;

alter table studio_branch
    drop constraint studio_branch_status_check;

alter table studio_branch
    add constraint studio_branch_status_check
    check (status in ('draft', 'replayed', 'failed', 'promoted'));

insert into schema_migrations (version) values ('0032_studio_branch_failed_status')
on conflict (version) do nothing;

commit;
