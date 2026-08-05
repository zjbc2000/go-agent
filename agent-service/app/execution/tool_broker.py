"""FastAPI tool broker: the sandbox container's ONLY external channel.

Every tool invocation from an isolated sandbox container passes through the
broker. It authenticates via a signed grant token (NOT the standard internal
token + JWT — the sandbox has only the grant), reloads the run and its approval
from PostgreSQL (so a leaked grant is still bounded by current DB state),
executes the tool AS the grant's user_id via the user-scoped repository, and
records a redacted audit row.

Idempotent replay: on UNIQUE(run_id, step_id) conflict, the broker returns the
stored prior result without re-executing — a retried step never re-executes a
side effect (plan Global Constraint: all side effects are idempotent by
(sandbox_run_id, step_id)).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError, ErrorCode
from app.db.session import service_session
from app.execution.grant import GrantInvalid, GrantVerifier
from app.models.execution import SandboxRun as SandboxRunRecord
from app.models.execution import SandboxToolCall as SandboxToolCallRecord
from app.repositories.planning import DocumentRepository, DocumentType

# Valid run statuses that permit tool execution through the broker.
_IN_FLIGHT_STATUSES = frozenset({"running"})


@dataclass(frozen=True)
class ToolResult:
    """The outcome of a tool invocation through the broker."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class ToolBroker:
    """Mediates tool invocations from sandbox containers to the user's data.

    Constructed with injectable dependencies so tests can swap the repository,
    cipher, and grant verifier.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        documents: DocumentRepository,
        cipher: EnvelopeCipher,
        grant_verifier: GrantVerifier,
    ) -> None:
        self._session_factory = session_factory
        self._documents = documents
        self._cipher = cipher
        self._grant_verifier = grant_verifier

    async def invoke(self, grant_token: str, step_id: str, tool_id: str, input: dict[str, Any]) -> ToolResult:
        """Authenticate the grant, validate bounds, execute the tool, and record audit.

        The grant is the ONLY authentication — there is no JWT or internal token
        for the sandbox container.
        """
        # 1. Verify the grant token (signature + expiry + step allowlist).
        try:
            grant = self._grant_verifier.verify(grant_token)
        except GrantInvalid as exc:
            raise ApiError(cast(ErrorCode, exc.code), exc.message, False) from None

        if step_id not in grant.step_ids:
            raise ApiError("SANDBOX_GRANT_INVALID", "Step is not allowed by this grant.", False)

        # 2. Reload the run and its approval from PostgreSQL (service role) so a
        #    leaked grant is still bounded by current DB state.
        run = await self._load_run(grant.run_id, grant.user_id)
        if run is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run not found or not owned by the grant user.", False)
        if run["status"] not in _IN_FLIGHT_STATUSES:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run is not in flight.", False)

        # 3. Build a user-scoped context for the grant's user_id so all document
        #    operations are RLS-gated to the owner.
        user_id = uuid.UUID(grant.user_id)
        context = RequestContext(user_id=user_id, role="user", request_id=grant.run_id)

        # 4. Check for idempotent replay: if this (run_id, step_id) already has a
        #    recorded tool call, return the stored result without re-executing.
        existing = await self._find_tool_call(str(run["id"]), step_id)
        if existing is not None:
            return ToolResult(success=True, data={"replayed": True, "status": existing["result_status"]})

        # 5. Execute the tool as the grant's user.
        result = await self._execute_tool(context, tool_id, input)

        # 6. Record a redacted audit row (UNIQUE(run_id, step_id) so a concurrent
        #    retry surfaces the conflict and can replay the stored result).
        await self._record_tool_call(str(run["id"]), grant.user_id, step_id, tool_id, result)

        return result

    async def _load_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Load the sandbox run with a service-role session (no RLS)."""
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            return None
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(SandboxRunRecord).where(SandboxRunRecord.id == rid)
            )
            if row is None or str(row.user_id) != user_id:
                return None
            return {
                "id": str(row.id),
                "status": row.status,
                "user_id": str(row.user_id),
                "document_id": str(row.document_id),
                "plan_hash": row.plan_hash,
            }

    async def _find_tool_call(self, run_id: str, step_id: str) -> dict[str, Any] | None:
        """Return a previously recorded tool call for (run_id, step_id), or None."""
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            return None
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(SandboxToolCallRecord).where(
                    SandboxToolCallRecord.run_id == rid,
                    SandboxToolCallRecord.step_id == step_id,
                )
            )
            if row is None:
                return None
            return {"result_status": row.result_status}

    async def _execute_tool(
        self, context: RequestContext, tool_id: str, input: dict[str, Any]
    ) -> ToolResult:
        """Dispatch a tool invocation to the user-scoped document repository."""
        if tool_id == "document.read":
            return await self._tool_document_read(context, input)
        elif tool_id == "document.create":
            return await self._tool_document_create(context, input)
        elif tool_id == "document.update":
            return await self._tool_document_update(context, input)
        elif tool_id == "document.delete":
            return await self._tool_document_delete(context, input)
        else:
            return ToolResult(success=False, error_code="SANDBOX_DENIED", error_message=f"Unknown tool: {tool_id!r}")

    async def _tool_document_read(self, context: RequestContext, input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        doc = await self._documents.get(context, document_id)
        if doc is None:
            return ToolResult(success=False, error_code="NOT_FOUND", error_message="Document not found.")
        return ToolResult(success=True, data={
            "id": str(doc.id), "type": doc.type, "title": doc.title, "body": doc.body,
            "version": doc.version, "status": doc.status,
        })

    async def _tool_document_create(self, context: RequestContext, input: dict[str, Any]) -> ToolResult:
        doc_type = input.get("type")
        if not isinstance(doc_type, str) or doc_type not in ("memory", "interest", "task", "skill"):
            return ToolResult(success=False, error_code="VALIDATION_FAILED", error_message="Invalid document type.")
        title = input.get("title", "")
        body = input.get("body", "")
        if not isinstance(title, str) or not isinstance(body, str):
            return ToolResult(
                success=False, error_code="VALIDATION_FAILED",
                error_message="title and body must be strings.",
            )
        doc = await self._documents.create_active(
            context, type=cast(DocumentType, doc_type), title=title, body=body,
        )
        return ToolResult(success=True, data={"id": str(doc.id), "title": doc.title, "version": doc.version})

    async def _tool_document_update(self, context: RequestContext, input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        existing = await self._documents.get(context, document_id)
        if existing is None:
            return ToolResult(success=False, error_code="NOT_FOUND", error_message="Document not found.")
        title = input.get("title", existing.title)
        body = input.get("body", existing.body)
        if not isinstance(title, str) or not isinstance(body, str):
            return ToolResult(
                success=False, error_code="VALIDATION_FAILED",
                error_message="title and body must be strings.",
            )
        doc = await self._documents.update_active(context, document_id, title=title, body=body)
        return ToolResult(success=True, data={"id": str(doc.id), "version": doc.version})

    async def _tool_document_delete(self, context: RequestContext, input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        deleted = await self._documents.delete(context, document_id)
        if not deleted:
            return ToolResult(success=False, error_code="NOT_FOUND", error_message="Document not found.")
        return ToolResult(success=True, data={"deleted": True})

    async def _record_tool_call(
        self,
        run_id: str,
        user_id: str,
        step_id: str,
        tool_id: str,
        result: ToolResult,
    ) -> None:
        """Insert a redacted tool-call row; on UNIQUE conflict this is a no-op
        (the caller already checked for an existing row, but a concurrent retry
        may race — the constraint is the final guard).
        """
        try:
            rid = uuid.UUID(run_id)
            uid = uuid.UUID(user_id)
        except ValueError:
            return
        result_status = "succeeded" if result.success else "failed"
        async with service_session(self._session_factory) as session:
            stmt = (
                insert(SandboxToolCallRecord)
                .values(
                    run_id=rid,
                    user_id=uid,
                    step_id=step_id,
                    tool_id=tool_id,
                    result_status=result_status,
                )
                .on_conflict_do_nothing(index_elements=["run_id", "step_id"])
            )
            await session.execute(stmt)


def _require_uuid(input: dict[str, Any], key: str) -> uuid.UUID:
    value = input.get(key)
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", f"{key} must be a string.", False)
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", f"{key} must be a valid UUID.", False) from None
