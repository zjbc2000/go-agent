-- Task 1 execution slice: declarative skill manifests compile into immutable
-- execution plans; write/delete steps wait for an expiring execution approval,
-- read-only steps queue a sandbox run directly. The outbox and the MCP registry
-- are provisioned now and get behavior in Tasks 2/4.
--
-- Skills ARE planning documents of category 'skill': the JSON manifest lives as
-- the document's body ciphertext (document_versions), bound to the exact
-- document_version the compiler hashed. Every row here is user-owned and RLS
-- protected by the end-user JWT (auth.uid()). Raw columns never hold plaintext:
-- inputs are stored only as KMS-envelope-encrypted ciphertext.

-- An approval to run a write/delete skill execution; expires so stale approvals
-- cannot hang forever. Decision columns mirror the planning approvals so Task 3
-- (execution decisions) reuses the same resolution shape.
create table public.execution_approvals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  document_id uuid not null references public.documents(id) on delete cascade,
  version_id uuid not null,
  plan_hash text not null,
  inputs_ciphertext text not null,
  status text not null default 'pending'
    check (status in ('pending', 'confirmed', 'rejected', 'superseded')),
  decision text,
  idempotency_key text,
  expires_at timestamptz,
  resolved_by uuid,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Idempotency: at most one decision result per (approval, idempotency_key),
-- following the planning-approvals pattern.
create unique index execution_approvals_id_idempotency_key_idx
  on public.execution_approvals (id, idempotency_key);
create index execution_approvals_user_id_idx on public.execution_approvals (user_id);
create index execution_approvals_document_id_idx on public.execution_approvals (document_id);

-- A queued execution of an immutable plan. Write/delete executions create their
-- sandbox run only after their approval resolves; read-only executions queue
-- immediately. error_message is only ever redacted/safe text. idempotency_key
-- lets a repeated request return the same run instead of duplicating it.
create table public.sandbox_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  document_id uuid not null references public.documents(id) on delete cascade,
  version_id uuid not null,
  plan_hash text not null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'timed_out', 'policy_denied')),
  approval_id uuid references public.execution_approvals(id) on delete set null,
  inputs_ciphertext text not null,
  error_code text,
  error_message text,
  idempotency_key text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index sandbox_runs_id_idempotency_key_idx
  on public.sandbox_runs (id, idempotency_key);
create index sandbox_runs_user_id_idx on public.sandbox_runs (user_id);
create index sandbox_runs_document_id_idx on public.sandbox_runs (document_id);

-- Transactional outbox for Task 2 (event publication). Owned rows carry user_id
-- and are RLS protected; service_role publishes on the user's behalf.
create table public.outbox_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  event_type text not null,
  aggregate_id uuid,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'published')),
  attempts int not null default 0,
  next_attempt_at timestamptz,
  published_at timestamptz,
  created_at timestamptz not null default now()
);

create index outbox_events_user_id_idx on public.outbox_events (user_id);
create index outbox_events_status_idx on public.outbox_events (status);

-- MCP registry for Task 4: a user's servers and the tools each exposes. No
-- behavior yet; the schema fixes the shape the compiler gates allowed_tools
-- against.
create table public.mcp_servers (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  name text not null,
  image text not null,
  enabled boolean not null default true,
  provenance text,
  sbom text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index mcp_servers_user_id_idx on public.mcp_servers (user_id);

create table public.mcp_tools (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  server_id uuid not null references public.mcp_servers(id) on delete cascade,
  tool_id text not null,
  enabled boolean not null default true,
  input_schema jsonb not null default '{}'::jsonb,
  output_schema jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index mcp_tools_user_id_idx on public.mcp_tools (user_id);
create index mcp_tools_server_id_idx on public.mcp_tools (server_id);

alter table public.execution_approvals enable row level security;
alter table public.sandbox_runs enable row level security;
alter table public.outbox_events enable row level security;
alter table public.mcp_servers enable row level security;
alter table public.mcp_tools enable row level security;

create policy "execution_approvals owner select" on public.execution_approvals for select using (user_id = auth.uid());
create policy "execution_approvals owner insert" on public.execution_approvals for insert with check (user_id = auth.uid());
create policy "execution_approvals owner update" on public.execution_approvals for update using (user_id = auth.uid());
create policy "execution_approvals owner delete" on public.execution_approvals for delete using (user_id = auth.uid());

create policy "sandbox_runs owner select" on public.sandbox_runs for select using (user_id = auth.uid());
create policy "sandbox_runs owner insert" on public.sandbox_runs for insert with check (user_id = auth.uid());
create policy "sandbox_runs owner update" on public.sandbox_runs for update using (user_id = auth.uid());
create policy "sandbox_runs owner delete" on public.sandbox_runs for delete using (user_id = auth.uid());

create policy "outbox_events owner select" on public.outbox_events for select using (user_id = auth.uid());
create policy "outbox_events owner insert" on public.outbox_events for insert with check (user_id = auth.uid());
create policy "outbox_events owner update" on public.outbox_events for update using (user_id = auth.uid());
create policy "outbox_events owner delete" on public.outbox_events for delete using (user_id = auth.uid());

create policy "mcp_servers owner select" on public.mcp_servers for select using (user_id = auth.uid());
create policy "mcp_servers owner insert" on public.mcp_servers for insert with check (user_id = auth.uid());
create policy "mcp_servers owner update" on public.mcp_servers for update using (user_id = auth.uid());
create policy "mcp_servers owner delete" on public.mcp_servers for delete using (user_id = auth.uid());

create policy "mcp_tools owner select" on public.mcp_tools for select using (user_id = auth.uid());
create policy "mcp_tools owner insert" on public.mcp_tools for insert with check (user_id = auth.uid());
create policy "mcp_tools owner update" on public.mcp_tools for update using (user_id = auth.uid());
create policy "mcp_tools owner delete" on public.mcp_tools for delete using (user_id = auth.uid());

-- Supabase does not grant table privileges to new public tables by default; without
-- these grants the RLS policies above are unreachable ("permission denied for table ...").
-- Row access is still enforced by RLS. service_role publishes outbox events on the
-- user's behalf in Task 2.
grant select, insert, update, delete on table public.execution_approvals to authenticated;
grant select, insert, update, delete on table public.sandbox_runs to authenticated;
grant select, insert, update, delete on table public.outbox_events to authenticated;
grant select, insert, update, delete on table public.mcp_servers to authenticated;
grant select, insert, update, delete on table public.mcp_tools to authenticated;

grant select, insert, update, delete on table public.execution_approvals to service_role;
grant select, insert, update, delete on table public.sandbox_runs to service_role;
grant select, insert, update, delete on table public.outbox_events to service_role;
grant select, insert, update, delete on table public.mcp_servers to service_role;
grant select, insert, update, delete on table public.mcp_tools to service_role;
