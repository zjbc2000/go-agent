-- Task 3 sandbox slice: lease-based claim recovery and redacted tool-call audit.
--
-- I1 (Task-2 review): a worker that crashes after claiming a run (queued->running)
-- leaves the run permanently stuck. This migration adds ``sandbox_runs.claimed_at``
-- so the worker's claim becomes a lease: a stale running row whose claimed_at is
-- older than the lease TTL is re-claimable. A runtime step timeout maps to
-- ``timed_out``.
--
-- Also adds ``sandbox_tool_calls``: a redacted, idempotent audit trail of every
-- tool invocation a sandbox container makes through the broker. The UNIQUE
-- (run_id, step_id) constraint makes every side effect idempotent by run+step —
-- a retried step returns the stored prior result and never re-executes a side
-- effect (plan Global Constraint). Raw input content is never stored.
--
-- RLS owner policies + explicit grants follow the Plan-1/2 pattern.

alter table public.sandbox_runs
  add column claimed_at timestamptz;

-- A run that was claimed but whose worker died will have claimed_at set but
-- finished_at null; the worker's claim predicate uses this column to decide
-- whether a running row is stale.

create table public.sandbox_tool_calls (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.sandbox_runs(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  step_id text not null,
  tool_id text not null,
  result_status text not null,
  created_at timestamptz not null default now(),
  unique (run_id, step_id)
);

create index sandbox_tool_calls_run_id_idx on public.sandbox_tool_calls (run_id);
create index sandbox_tool_calls_user_id_idx on public.sandbox_tool_calls (user_id);

alter table public.sandbox_tool_calls enable row level security;

create policy "sandbox_tool_calls owner select" on public.sandbox_tool_calls for select using (user_id = auth.uid());
create policy "sandbox_tool_calls owner insert" on public.sandbox_tool_calls for insert with check (user_id = auth.uid());
create policy "sandbox_tool_calls owner update" on public.sandbox_tool_calls for update using (user_id = auth.uid());
create policy "sandbox_tool_calls owner delete" on public.sandbox_tool_calls for delete using (user_id = auth.uid());

grant select, insert, update, delete on table public.sandbox_tool_calls to authenticated;
grant select, insert, update, delete on table public.sandbox_tool_calls to service_role;
