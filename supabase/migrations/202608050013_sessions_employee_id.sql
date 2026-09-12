-- Employee sessions: mark a chat session as bound to an employee so the assistant
-- graph can inject ONLY that employee's persona prompt (never the planning assistant
-- SOUL) when generating inside the session. NULL = a normal 苟蛋 session.
--
-- The column is nullable and FK-safe (on delete set null); there is no hard employee
-- delete in this MVP, but the FK keeps referential integrity if one is added later.

alter table public.sessions
  add column employee_id uuid references public.employees(id) on delete set null;

create index sessions_employee_id_idx on public.sessions (employee_id);

-- Re-grant after adding the column so the roles can read it (same idiom as 010).
grant select, insert, update, delete on table public.sessions to authenticated;
grant select, insert, update, delete on table public.sessions to service_role;
