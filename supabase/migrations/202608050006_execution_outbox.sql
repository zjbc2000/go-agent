-- Task 2 execution slice: tighten execution idempotency and version lookup.
--
-- The Task-1 review flagged that the unique indexes on (id, idempotency_key)
-- were trivially unique (id is the primary key), so two concurrent same-key
-- requests could each find no existing row and double-create an approval/run.
-- Replace them with a document-scoped unique key on BOTH tables so the database
-- enforces at most one row per (document_id, idempotency_key).
--
-- Postgres UNIQUE allows multiple NULL idempotency_key rows per document, so
-- rows that never carry a request key are unaffected.
--
-- Version-scoped lookups also gain indexes: the worker loads a run's document
-- version by id and the service scans approvals/runs by version.

drop index public.execution_approvals_id_idempotency_key_idx;
drop index public.sandbox_runs_id_idempotency_key_idx;

create unique index execution_approvals_document_id_idempotency_key_idx
  on public.execution_approvals (document_id, idempotency_key);
create unique index sandbox_runs_document_id_idempotency_key_idx
  on public.sandbox_runs (document_id, idempotency_key);

create index execution_approvals_version_id_idx on public.execution_approvals (version_id);
create index sandbox_runs_version_id_idx on public.sandbox_runs (version_id);
