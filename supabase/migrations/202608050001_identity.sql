-- Profiles: one row per auth user carrying the application role and display name.

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('user', 'admin')) default 'user',
  display_name text not null,
  disabled_at timestamptz
);

alter table public.profiles enable row level security;

-- A profile owner may read their own row (RLS uses the end-user JWT).
create policy "profile owner reads self" on public.profiles
  for select using (id = auth.uid());

-- A profile owner may create their own row on first login.
create policy "profile owner inserts self" on public.profiles
  for insert with check (id = auth.uid());

-- Supabase does not grant table privileges to new public tables by default; without
-- these grants the RLS policies above are unreachable ("permission denied for table
-- profiles"). Grants stay minimal and row access is still enforced by RLS.
grant select, insert on table public.profiles to authenticated;
grant select, insert, update on table public.profiles to service_role;

create index profiles_role_idx on public.profiles (role);
