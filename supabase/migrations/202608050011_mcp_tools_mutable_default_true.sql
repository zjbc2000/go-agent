-- CARRIED from Task 4 (NEW Minor): close the storage-layer fail-open gap.
--
-- ``McpToolSchema.mutable`` already defaults to ``True`` (Pydantic, fail-closed),
-- but the ``mcp_tools.mutable`` column (migration 010) and the ORM model still
-- default to ``false``. ``register_mcp`` is currently the sole writer and always
-- sets the value explicitly, so this is unreachable today — but any non-manifest
-- insert would silently default to a READ tool (no execution approval required).
-- Flip the column default to match the schema default: a tool is a WRITE
-- (approval-gated) unless the manifest explicitly attests ``mutable: false``.
alter table public.mcp_tools alter column mutable set default true;
