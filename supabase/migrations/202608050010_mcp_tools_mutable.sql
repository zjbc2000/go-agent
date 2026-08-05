-- NEW #7: the write/approval gate must cover MCP write tools, not just
-- document.*.  The ``mutable`` column marks MCP tools whose invocations are
-- gated behind a 15-min execution approval, matching the document.create/
-- update/delete approval path. Read-only MCP tools (mutable=false) run
-- without an approval.
alter table public.mcp_tools
  add column mutable boolean not null default false;

-- Unique indices so the registry can upsert server rows by (user_id, name)
-- and tool rows by (user_id, server_id, tool_id) idempotently.
create unique index if not exists mcp_servers_user_id_name_idx
  on public.mcp_servers (user_id, name);
create unique index if not exists mcp_tools_user_id_server_id_tool_id_idx
  on public.mcp_tools (user_id, server_id, tool_id);

-- Re-grant after adding the column so the role can read it.
grant select, insert, update, delete on table public.mcp_tools to authenticated;
grant select, insert, update, delete on table public.mcp_tools to service_role;
