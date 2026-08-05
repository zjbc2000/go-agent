-- Versioned planning documents: documents, document_versions, document_drafts,
-- approvals, audit_logs.
--
-- Formal planning text (title/body) is stored ONLY as KMS-envelope-encrypted
-- ciphertext; raw columns never contain plaintext. Every versioned write appends
-- a new document_versions row; a write never mutates an existing version. Every
-- user-domain row carries user_id and is protected by RLS using the end-user JWT
-- (auth.uid()). There is intentionally NO unscoped/admin read path for planning
-- content in this MVP; all reads evaluate RLS as the caller.

create table public.documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  type text not null check (type in ('memory', 'interest', 'task', 'skill')),
  current_version integer not null default 1,
  status text not null check (status = 'active'),
  title_ciphertext text not null,
  body_ciphertext text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.document_versions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  document_id uuid not null references public.documents(id) on delete cascade,
  version integer not null,
  title_ciphertext text not null,
  body_ciphertext text not null,
  created_at timestamptz not null default now()
);

-- One version per document position; versions are immutable once written.
create unique index documents_version_idx on public.document_versions(document_id, version);

-- Pending drafts are not formal documents and are never injected into model
-- context. Columns/encryption/RLS are provisioned now; behavior lands in Task 2.
create table public.document_drafts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  document_id uuid not null references public.documents(id) on delete cascade,
  based_on_version integer not null,
  status text not null check (status in ('pending', 'confirmed', 'rejected')) default 'pending',
  title_ciphertext text not null,
  body_ciphertext text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Approval decisions over a draft (confirm / edit-confirm / reject).
create table public.approvals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  document_id uuid not null references public.documents(id) on delete cascade,
  draft_id uuid not null references public.document_drafts(id) on delete cascade,
  status text not null check (status in ('pending', 'confirmed', 'rejected')) default 'pending',
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Append-only action trail (confirm, edit-confirm, reject, regenerate, restore,
-- delete). document_id is nullable and set-null on delete so the trail survives a
-- document deletion: a delete action must stay auditable.
create table public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  document_id uuid references public.documents(id) on delete set null,
  action text not null,
  detail_ciphertext text,
  created_at timestamptz not null default now()
);

create index documents_user_id_idx on public.documents (user_id);
create index document_versions_user_id_idx on public.document_versions (user_id);
create index document_versions_document_id_idx on public.document_versions (document_id);
create index document_drafts_user_id_idx on public.document_drafts (user_id);
create index document_drafts_document_id_idx on public.document_drafts (document_id);
create index approvals_user_id_idx on public.approvals (user_id);
create index approvals_document_id_idx on public.approvals (document_id);
create index audit_logs_user_id_idx on public.audit_logs (user_id);
create index audit_logs_document_id_idx on public.audit_logs (document_id);

alter table public.documents enable row level security;
alter table public.document_versions enable row level security;
alter table public.document_drafts enable row level security;
alter table public.approvals enable row level security;
alter table public.audit_logs enable row level security;

create policy "documents owner select" on public.documents for select using (user_id = auth.uid());
create policy "documents owner insert" on public.documents for insert with check (user_id = auth.uid());
create policy "documents owner update" on public.documents for update using (user_id = auth.uid());
create policy "documents owner delete" on public.documents for delete using (user_id = auth.uid());

create policy "document_versions owner select" on public.document_versions for select using (user_id = auth.uid());
create policy "document_versions owner insert" on public.document_versions for insert with check (user_id = auth.uid());
create policy "document_versions owner update" on public.document_versions for update using (user_id = auth.uid());
create policy "document_versions owner delete" on public.document_versions for delete using (user_id = auth.uid());

create policy "document_drafts owner select" on public.document_drafts for select using (user_id = auth.uid());
create policy "document_drafts owner insert" on public.document_drafts for insert with check (user_id = auth.uid());
create policy "document_drafts owner update" on public.document_drafts for update using (user_id = auth.uid());
create policy "document_drafts owner delete" on public.document_drafts for delete using (user_id = auth.uid());

create policy "approvals owner select" on public.approvals for select using (user_id = auth.uid());
create policy "approvals owner insert" on public.approvals for insert with check (user_id = auth.uid());
create policy "approvals owner update" on public.approvals for update using (user_id = auth.uid());
create policy "approvals owner delete" on public.approvals for delete using (user_id = auth.uid());

create policy "audit_logs owner select" on public.audit_logs for select using (user_id = auth.uid());
create policy "audit_logs owner insert" on public.audit_logs for insert with check (user_id = auth.uid());
create policy "audit_logs owner update" on public.audit_logs for update using (user_id = auth.uid());
create policy "audit_logs owner delete" on public.audit_logs for delete using (user_id = auth.uid());

-- Supabase does not grant table privileges to new public tables by default; without
-- these grants the RLS policies above are unreachable ("permission denied for table ...").
-- Row access is still enforced by RLS. service_role is reserved for narrow operational
-- jobs; planning reads/writes in this MVP are user-scoped only.
grant select, insert, update, delete on table public.documents to authenticated;
grant select, insert, update, delete on table public.document_versions to authenticated;
grant select, insert, update, delete on table public.document_drafts to authenticated;
grant select, insert, update, delete on table public.approvals to authenticated;
grant select, insert, update, delete on table public.audit_logs to authenticated;
grant select, insert, update, delete on table public.documents to service_role;
grant select, insert, update, delete on table public.document_versions to service_role;
grant select, insert, update, delete on table public.document_drafts to service_role;
grant select, insert, update, delete on table public.approvals to service_role;
grant select, insert, update, delete on table public.audit_logs to service_role;
