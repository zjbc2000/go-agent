"""MCP registry: admission, discovery, and mutability lookups.

``register_mcp`` validates a manifest (digest pinning, provenance, sbom) and
upserts the ``mcp_servers`` + ``mcp_tools`` rows in one user-scoped transaction.
Duplicate registration is idempotent (same server name → upsert the row).

``discover_tools`` returns only the user's enabled tools for a given server.
Cross-user lookups return empty (NOT_FOUND).

``is_tool_mutable`` / ``is_tool_allowed`` are efficient lookups for the broker
and service — they check registration, enabled, and mutability against the DB.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.context import RequestContext
from app.core.errors import ApiError
from app.db.session import service_session, user_scoped_session
from app.models.execution import McpServer as McpServerRecord
from app.models.execution import McpTool as McpToolRecord


class McpRegistry:
    """Admission, discovery, and mutability for user-registered MCP servers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ---- Registration ---------------------------------------------------

    async def register_mcp(
        self, context: RequestContext, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate (digest, provenance, sbom) and upsert server + tools.

        Returns a safe dict with server id, name, and tool_ids.
        Duplicate registration (same name) is idempotent.
        """
        from app.mcp.schemas import McpManifest

        try:
            parsed = McpManifest.model_validate(manifest)
        except Exception:
            raise ApiError(
                "MCP_DIGEST_REQUIRED",
                "Manifest invalid: image must be oci://...@sha256:<hex64>, "
                "provenance and sbom non-empty.",
                False,
            ) from None

        async with user_scoped_session(self._session_factory, context) as session:
            # Upsert the server row (idempotent by user_id + name).
            server_stmt = (
                insert(McpServerRecord)
                .values(
                    user_id=context.user_id,
                    name=parsed.name,
                    image=parsed.image,
                    enabled=True,
                    provenance=parsed.provenance,
                    sbom=parsed.sbom,
                )
                .on_conflict_do_update(
                    index_elements=["user_id", "name"],
                    set_=dict(
                        image=parsed.image,
                        provenance=parsed.provenance,
                        sbom=parsed.sbom,
                        enabled=True,
                    ),
                )
                .returning(McpServerRecord.id)
            )
            server_id = (await session.execute(server_stmt)).scalar_one()

            # Upsert tools (idempotent by user_id + server_id + tool_id).
            tool_ids: list[str] = []
            for t in parsed.tools:
                tool_stmt = (
                    insert(McpToolRecord)
                    .values(
                        user_id=context.user_id,
                        server_id=server_id,
                        tool_id=t.tool_id,
                        enabled=True,
                        mutable=t.mutable,
                        input_schema=t.input_schema,
                        output_schema=t.output_schema,
                    )
                    .on_conflict_do_update(
                        index_elements=["user_id", "server_id", "tool_id"],
                        set_=dict(
                            mutable=t.mutable,
                            input_schema=t.input_schema,
                            output_schema=t.output_schema,
                            enabled=True,
                        ),
                    )
                )
                await session.execute(tool_stmt)
                tool_ids.append(t.tool_id)

        return {
            "id": str(server_id),
            "name": parsed.name,
            "tool_ids": tool_ids,
        }

    # ---- Discovery ------------------------------------------------------

    async def discover_tools(
        self, context: RequestContext, server_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Return the user's enabled tools for the given server.

        Cross-user lookups return an empty list (RLS + user-scoped session).
        """
        async with user_scoped_session(self._session_factory, context) as session:
            server = await session.scalar(
                select(McpServerRecord).where(
                    McpServerRecord.id == server_id,
                    McpServerRecord.user_id == context.user_id,
                )
            )
            if server is None:
                return []
            tools = (
                await session.scalars(
                    select(McpToolRecord).where(
                        McpToolRecord.server_id == server_id,
                        McpToolRecord.user_id == context.user_id,
                        McpToolRecord.enabled.is_(True),
                    )
                )
            ).all()
            return [
                {
                    "id": str(t.id),
                    "tool_id": t.tool_id,
                    "mutable": t.mutable,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                }
                for t in tools
            ]

    # ---- Allowlist / mutability lookups (for broker + service) -----------

    async def is_tool_allowed(
        self, context: RequestContext, tool_id: str
    ) -> bool:
        """True when ``tool_id`` is a user-registered AND enabled MCP tool."""
        async with user_scoped_session(self._session_factory, context) as session:
            row = await session.scalar(
                select(McpToolRecord).where(
                    McpToolRecord.user_id == context.user_id,
                    McpToolRecord.tool_id == tool_id,
                    McpToolRecord.enabled.is_(True),
                ).order_by(McpToolRecord.created_at.asc()).limit(1)
            )
            return row is not None

    async def is_tool_mutable(self, user_id: uuid.UUID, tool_id: str) -> bool:
        """True when ``tool_id`` is a mutable MCP tool for the user.

        Used by the broker (service-scoped session) during per-call approval
        re-check (NEW #7).  Deterministic ordering (created_at) so a tool-id
        collision cannot flip the approval decision by row order.
        """
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(McpToolRecord.mutable).where(
                    McpToolRecord.user_id == user_id,
                    McpToolRecord.tool_id == tool_id,
                    McpToolRecord.enabled.is_(True),
                ).order_by(McpToolRecord.created_at.asc()).limit(1)
            )
            return bool(row)

    async def get_tool_registration(
        self, user_id: uuid.UUID, tool_id: str
    ) -> dict[str, Any] | None:
        """Return the enabled tool row (service-scoped) for broker dispatch.

        Checks: server enabled AND tool enabled. Returns the tool's server
        image, output_schema, etc. for dispatch + validation.
        """
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(McpToolRecord).where(
                    McpToolRecord.user_id == user_id,
                    McpToolRecord.tool_id == tool_id,
                    McpToolRecord.enabled.is_(True),
                ).order_by(McpToolRecord.created_at.asc()).limit(1)
            )
            if row is None:
                return None
            # Verify the server is also enabled.
            server = await session.scalar(
                select(McpServerRecord).where(
                    McpServerRecord.id == row.server_id,
                    McpServerRecord.user_id == user_id,
                    McpServerRecord.enabled.is_(True),
                )
            )
            if server is None:
                return None
            return {
                "tool_id": row.tool_id,
                "mutable": row.mutable,
                "server_image": server.image,
                "input_schema": row.input_schema,
                "output_schema": row.output_schema,
                "server_id": str(server.id),
            }
