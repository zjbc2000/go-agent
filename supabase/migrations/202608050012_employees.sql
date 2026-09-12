-- Company/employees: employees + employee_approvals.
--
-- Name/position/prompt are stored ONLY as KMS-envelope-encrypted ciphertext; raw
-- columns never contain plaintext (same rule as planning documents). status is the
-- lifecycle flag: 'active' (在职) or 'inactive' (已离职). A fire approval sets
-- inactive, a rehire sets active, an adjust_position rewrites the position.
--
-- employee_approvals mirrors the execution_approvals decision pattern (locked
-- FOR UPDATE, pending/expiry/conflict, idempotency_key), but is dedicated to
-- employee actions so it stays independent of planning's document-coupled
-- approvals table. Every user-domain row is RLS-protected by the end-user JWT.

create table public.employees (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  status text not null check (status in ('active', 'inactive')) default 'active',
  name_ciphertext text not null,
  position_ciphertext text not null,
  prompt_ciphertext text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.employee_approvals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  employee_id uuid not null references public.employees(id) on delete cascade,
  action text not null check (action in ('fire', 'rehire', 'adjust_position')),
  position_ciphertext text,
  payload_ciphertext text not null,
  payload_sha256 text not null,
  status text not null check (status in ('pending', 'confirmed', 'rejected')) default 'pending',
  decision text,
  idempotency_key text,
  expires_at timestamptz,
  run_id uuid,
  resolved_by uuid,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index employees_user_id_idx on public.employees (user_id);
create index employee_approvals_user_id_idx on public.employee_approvals (user_id);
create index employee_approvals_employee_id_idx on public.employee_approvals (employee_id);
create unique index employee_approvals_id_idempotency_key_idx on public.employee_approvals (id, idempotency_key);

alter table public.employees enable row level security;
alter table public.employee_approvals enable row level security;

create policy "employees owner select" on public.employees for select using (user_id = auth.uid());
create policy "employees owner insert" on public.employees for insert with check (user_id = auth.uid());
create policy "employees owner update" on public.employees for update using (user_id = auth.uid());
create policy "employees owner delete" on public.employees for delete using (user_id = auth.uid());

create policy "employee_approvals owner select" on public.employee_approvals for select using (user_id = auth.uid());
create policy "employee_approvals owner insert" on public.employee_approvals for insert with check (user_id = auth.uid());
create policy "employee_approvals owner update" on public.employee_approvals for update using (user_id = auth.uid());
create policy "employee_approvals owner delete" on public.employee_approvals for delete using (user_id = auth.uid());

-- Supabase does not grant table privileges to new public tables by default; without
-- these grants the RLS policies above are unreachable ("permission denied for table ...").
-- Row access is still enforced by RLS. service_role is reserved for narrow
-- operational jobs; employee reads/writes in this MVP are user-scoped only.
grant select, insert, update, delete on table public.employees to authenticated;
grant select, insert, update, delete on table public.employee_approvals to authenticated;
grant select, insert, update, delete on table public.employees to service_role;
grant select, insert, update, delete on table public.employee_approvals to service_role;
