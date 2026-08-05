-- Task 3 round-2 fix: tighten sandbox_tool_calls RLS to prevent cross-user
-- reservation squat (NEW #2).
--
-- The original policies checked only ``user_id = auth.uid()``, which means any
-- authenticated user could insert a row for another user's run (the user_id
-- matches the squatter's auth.uid() but the run belongs to someone else).
-- Replace the insert/update/delete policies with run-ownership predicates:
-- the user must own both the run AND the tool-call row.

drop policy if exists "sandbox_tool_calls owner insert" on public.sandbox_tool_calls;
drop policy if exists "sandbox_tool_calls owner update" on public.sandbox_tool_calls;
drop policy if exists "sandbox_tool_calls owner delete" on public.sandbox_tool_calls;

create policy "sandbox_tool_calls owner insert"
  on public.sandbox_tool_calls for insert
  with check (
    user_id = auth.uid()
    and run_id in (select id from sandbox_runs where user_id = auth.uid())
  );

create policy "sandbox_tool_calls owner update"
  on public.sandbox_tool_calls for update
  using (
    user_id = auth.uid()
    and run_id in (select id from sandbox_runs where user_id = auth.uid())
  );

create policy "sandbox_tool_calls owner delete"
  on public.sandbox_tool_calls for delete
  using (
    user_id = auth.uid()
    and run_id in (select id from sandbox_runs where user_id = auth.uid())
  );
