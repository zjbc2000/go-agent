-- Encrypted recoverable chat persistence: sessions, messages, agent_runs, stream_events.
--
-- Message bodies and stream-event payloads are stored ONLY as KMS-envelope-encrypted
-- ciphertext; raw columns never contain plaintext. Every user-domain row carries
-- user_id and is protected by RLS using the end-user JWT (auth.uid()).

create table public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  title text not null default 'New chat',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  session_id uuid not null,
  idempotency_key text not null,
  status text not null
    check (status in ('queued', 'streaming', 'waiting_approval', 'completed', 'failed', 'cancelled'))
    default 'queued',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_runs_user_id_idempotency_key_key unique (user_id, idempotency_key)
);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  session_id uuid not null,
  run_id uuid,
  role text not null check (role in ('user', 'assistant', 'system')),
  content_ciphertext text not null,
  status text not null
    check (status in ('queued', 'streaming', 'completed', 'failed'))
    default 'completed',
  sequence bigint not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.stream_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  run_id uuid not null,
  kind text not null,
  payload_ciphertext text not null,
  sequence bigint not null,
  created_at timestamptz not null default now(),
  constraint stream_events_run_id_sequence_key unique (run_id, sequence)
);

create index sessions_user_id_idx on public.sessions (user_id);
create index agent_runs_user_id_idx on public.agent_runs (user_id);
create index agent_runs_session_id_idx on public.agent_runs (session_id);
create index messages_user_id_idx on public.messages (user_id);
create index messages_session_id_idx on public.messages (session_id);
create index messages_run_id_idx on public.messages (run_id);
create index stream_events_user_id_idx on public.stream_events (user_id);
create index stream_events_run_id_idx on public.stream_events (run_id);

alter table public.sessions enable row level security;
alter table public.agent_runs enable row level security;
alter table public.messages enable row level security;
alter table public.stream_events enable row level security;

create policy "sessions owner select" on public.sessions for select using (user_id = auth.uid());
create policy "sessions owner insert" on public.sessions for insert with check (user_id = auth.uid());
create policy "sessions owner update" on public.sessions for update using (user_id = auth.uid());
create policy "sessions owner delete" on public.sessions for delete using (user_id = auth.uid());

create policy "agent_runs owner select" on public.agent_runs for select using (user_id = auth.uid());
create policy "agent_runs owner insert" on public.agent_runs for insert with check (user_id = auth.uid());
create policy "agent_runs owner update" on public.agent_runs for update using (user_id = auth.uid());
create policy "agent_runs owner delete" on public.agent_runs for delete using (user_id = auth.uid());

create policy "messages owner select" on public.messages for select using (user_id = auth.uid());
create policy "messages owner insert" on public.messages for insert with check (user_id = auth.uid());
create policy "messages owner update" on public.messages for update using (user_id = auth.uid());
create policy "messages owner delete" on public.messages for delete using (user_id = auth.uid());

create policy "stream_events owner select" on public.stream_events for select using (user_id = auth.uid());
create policy "stream_events owner insert" on public.stream_events for insert with check (user_id = auth.uid());
create policy "stream_events owner update" on public.stream_events for update using (user_id = auth.uid());
create policy "stream_events owner delete" on public.stream_events for delete using (user_id = auth.uid());

-- Supabase does not grant table privileges to new public tables by default; without
-- these grants the RLS policies above are unreachable ("permission denied for table ...").
-- Row access is still enforced by RLS. service_role is for narrow operational jobs
-- (e.g. appending stream events on behalf of a running agent).
grant select, insert, update, delete on all tables in schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to service_role;
