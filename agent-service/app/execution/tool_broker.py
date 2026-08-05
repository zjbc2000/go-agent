"""FastAPI tool broker: the sandbox container's ONLY external channel.

Every tool invocation from an isolated sandbox container passes through the
broker. It authenticates via a signed grant token (NOT the standard internal
token + JWT — the sandbox has only the grant), reloads the run and its approval
from PostgreSQL, recompiles the plan from the pinned document version to verify
the hash chain, binds tool dispatch AND input to the plan's declared step
(ignoring the caller-supplied tool_id and input — the plan pins everything at
compile time), executes the tool AS the grant's user_id via the user-scoped
repository, and records an idempotent audit row.

Idempotent replay (TOCTOU-safe): the broker reserves the sandbox_tool_calls row
(INSERT ... ON CONFLICT DO NOTHING RETURNING id) BEFORE executing the side
effect. Only the reservation winner executes; the winner finalizes the row with
the encrypted result in a try/finally so that a crash or failure still persists
a final state (never leaves a stuck "reserved" row). The loser path distinguishes
"reserved" (still in-progress → retryable error) from a finalized row (→ returns
the stored result).

Task 4: The broker is the SINGLE MCP mediation point. For MCP tool steps it
validates the server is enabled + the tool is enabled (MCP_TOOL_DISABLED),
re-checks the approval for mutable MCP steps (NEW #7), dispatches through an
injectable McpExecutor, and validates the untrusted output against the
registered output_schema.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError, ErrorCode
from app.db.session import service_session
from app.execution.grant import GrantInvalid, GrantVerifier
from app.models.execution import ExecutionApproval as ExecutionApprovalRecord
from app.models.execution import SandboxRun as SandboxRunRecord
from app.models.execution import SandboxToolCall as SandboxToolCallRecord
from app.models.planning import DocumentVersion as DocumentVersionRecord
from app.repositories.planning import DocumentRepository, DocumentType
from app.skills.compiler import WRITE_TOOLS, compile_skill
from app.skills.schemas import ExecutionPlan

if TYPE_CHECKING:
    from app.mcp.executor import McpExecutor
    from app.mcp.registry import McpRegistry
    from app.mcp.validator import McpValidator

_IN_FLIGHT_STATUSES = frozenset({"running"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ToolResult:
    """The outcome of a tool invocation through the broker."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class ToolBroker:
    """Mediates tool invocations from sandbox containers to the user's data."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        documents: DocumentRepository,
        cipher: EnvelopeCipher,
        grant_verifier: GrantVerifier,
        mcp_registry: McpRegistry | None = None,
        mcp_executor: McpExecutor | None = None,
        mcp_validator: McpValidator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._documents = documents
        self._cipher = cipher
        self._grant_verifier = grant_verifier
        self._mcp_registry = mcp_registry
        self._mcp_executor = mcp_executor
        self._mcp_validator = mcp_validator

    async def invoke(
        self, grant_token: str, step_id: str, tool_id: str, input: dict[str, Any]
    ) -> ToolResult:
        """Authenticate the grant, validate bounds, execute the tool, record audit.

        The grant is the ONLY authentication. The caller-supplied ``tool_id``
        must match the plan step, and the caller-supplied ``input`` is IGNORED
        — the broker executes with the plan's compiled step input (NEW #1).
        """
        # 1. Verify the grant token.
        try:
            grant = self._grant_verifier.verify(grant_token)
        except GrantInvalid as exc:
            raise ApiError(cast(ErrorCode, exc.code), exc.message, False) from None
        if step_id not in grant.step_ids:
            raise ApiError("SANDBOX_GRANT_INVALID", "Step is not allowed by this grant.", False)

        # 2. Reload the run.
        run = await self._load_run(grant.run_id, grant.user_id)
        if run is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run not found.", False)
        if run["status"] not in _IN_FLIGHT_STATUSES:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run is not in flight.", False)

        # 3. Recompile plan, verify hash chain, resolve step (tool + compiled input).
        plan = await self._recompile_and_verify(run, grant.plan_hash)
        step_tool, step_input = self._resolve_step(plan, step_id, tool_id)

        # 4. Approval check for write/delete steps + mutable MCP tools (NEW #7).
        if step_tool in WRITE_TOOLS:
            await self._verify_approval(run)
        elif self._mcp_registry is not None:
            if await self._mcp_registry.is_tool_mutable(
                uuid.UUID(grant.user_id), step_tool,
            ):
                await self._verify_approval(run)

        # 5. User-scoped context.
        user_id = uuid.UUID(grant.user_id)
        context = RequestContext(user_id=user_id, role="user", request_id=grant.run_id)

        # 6. Reserve the row BEFORE executing (I3 TOCTOU).
        run_id_str = str(run["id"])
        won = await self._reserve_tool_call(run_id_str, grant.user_id, step_id, step_tool)
        if not won:
            return await self._handle_loser(run_id_str, step_id, grant.user_id)

        # 7. Execute + finalize in a try/finally (NEW #3 MINOR): ANY exception
        #    (validation error, DB error, crash-like) persists a ``failed`` final
        #    state — never leaves a permanently stuck ``reserved`` row.
        try:
            result = await self._execute_tool(context, step_tool, step_input)
        except Exception:
            result = ToolResult(
                success=False, error_code="SANDBOX_ERROR",
                error_message="Tool execution raised an unexpected error.",
            )
            raise
        finally:
            # Always finalize: success stores the data; failure stores the error
            # (NEW #3 IMPORTANT: failed rows carry success=False on replay).
            await self._store_tool_call_result(run_id_str, step_id, result)

        return result

    # ---- Internal helpers ----------------------------------------------------

    async def _load_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
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
                "id": str(row.id), "status": row.status, "user_id": str(row.user_id),
                "document_id": str(row.document_id), "version_id": str(row.version_id),
                "plan_hash": row.plan_hash, "inputs_ciphertext": row.inputs_ciphertext,
                "approval_id": str(row.approval_id) if row.approval_id else None,
            }

    async def _verify_approval(self, run: dict[str, Any]) -> None:
        approval_id = run.get("approval_id")
        if not approval_id:
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Write step requires an execution approval.", False)
        try:
            aid = uuid.UUID(approval_id)
        except ValueError:
            raise ApiError("SANDBOX_GRANT_INVALID", "Invalid approval reference.", False)
        async with service_session(self._session_factory) as session:
            approval = await session.scalar(
                select(ExecutionApprovalRecord).where(ExecutionApprovalRecord.id == aid))
        if approval is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Execution approval not found.", False)
        if approval.status != "confirmed":
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Execution approval was not confirmed.", False)
        if approval.expires_at is not None and approval.expires_at <= _utcnow():
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Execution approval has expired.", False)

    async def _recompile_and_verify(
        self, run: dict[str, Any], grant_plan_hash: str
    ) -> ExecutionPlan:
        try:
            vid = uuid.UUID(run["version_id"])
            did = uuid.UUID(run["document_id"])
        except (ValueError, KeyError):
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Run references invalid document version.", False)
        async with service_session(self._session_factory) as session:
            body_ct = await session.scalar(
                select(DocumentVersionRecord.body_ciphertext).where(
                    DocumentVersionRecord.id == vid,
                    DocumentVersionRecord.document_id == did,
                ))
        if body_ct is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Document version not found.", False)
        manifest = json.loads(self._cipher.decrypt(body_ct))
        inputs_payload = self._cipher.decrypt_json(run["inputs_ciphertext"])
        inputs = inputs_payload.get("inputs", {}) if isinstance(inputs_payload, dict) else {}
        plan = compile_skill(manifest, inputs, version_id=vid)
        if plan.hash != run["plan_hash"]:
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Plan hash mismatch: recompiled != stored run hash.", False)
        if plan.hash != grant_plan_hash:
            raise ApiError("SANDBOX_GRANT_INVALID",
                           "Plan hash mismatch: grant != recompiled plan.", False)
        return plan

    def _resolve_step(
        self, plan: ExecutionPlan, step_id: str, caller_tool_id: str
    ) -> tuple[str, dict[str, Any]]:
        """Find the plan step by id; return (declared_tool, compiled_input).

        The caller-supplied tool_id must match the declared tool (I2).
        The CALLER-SUPPLIED INPUT IS DISCARDED — execution uses only the plan's
        compiled step input (NEW #1).
        """
        for step in plan.steps:
            if step.id == step_id:
                if step.tool != caller_tool_id:
                    raise ApiError(
                        "SANDBOX_GRANT_INVALID",
                        f"Step {step_id!r} tool mismatch: request says "
                        f"{caller_tool_id!r}, plan declares {step.tool!r}.",
                        False,
                    )
                return step.tool, step.input
        raise ApiError("SANDBOX_GRANT_INVALID",
                       f"Step {step_id!r} not found in the plan.", False)

    # ---- Reservation / idempotent replay --------------------------------

    async def _reserve_tool_call(
        self, run_id: str, user_id_str: str, step_id: str, tool_id: str
    ) -> bool:
        """Reserve the (run_id, step_id) row BEFORE executing.

        The insert carries the grant user's id — RLS + migration 009 ensure
        the user owns the run (NEW #2).
        """
        try:
            rid = uuid.UUID(run_id)
            uid = uuid.UUID(user_id_str)
        except ValueError:
            return False
        async with service_session(self._session_factory) as session:
            row = await session.execute(
                insert(SandboxToolCallRecord)
                .values(run_id=rid, user_id=uid, step_id=step_id,
                        tool_id=tool_id, result_status="reserved")
                .on_conflict_do_nothing(index_elements=["run_id", "step_id"])
                .returning(SandboxToolCallRecord.id))
            return row.first() is not None

    async def _handle_loser(
        self, run_id: str, step_id: str, grant_user_id: str
    ) -> ToolResult:
        """The row already exists — distinguish reserved vs finalized (NEW #3).

        Filters by ``grant_user_id`` to prevent cross-user poison (NEW #2).
        """
        row = await self._load_tool_call_row(run_id, step_id, grant_user_id)
        if row is None:
            return ToolResult(success=True, data={"replayed": True})
        if row["result_status"] == "reserved":
            return ToolResult(
                success=False, error_code="SANDBOX_STEP_IN_PROGRESS",
                error_message="This step is currently executing; retry.",
            )
        if row["result_ciphertext"] is not None:
            # NEW #3 IMPORTANT: return the stored result WITH the correct
            # success/failure status — a replayed failed step reports failure.
            return ToolResult(
                success=(row["result_status"] == "succeeded"),
                data=row["result_ciphertext"],
            )
        return ToolResult(success=row["result_status"] == "succeeded",
                          data={"replayed": True})

    async def _load_tool_call_row(
        self, run_id: str, step_id: str, grant_user_id: str
    ) -> dict[str, Any] | None:
        """Load the tool-call row, filtered by the grant's user_id (NEW #2)."""
        try:
            rid = uuid.UUID(run_id)
            uid = uuid.UUID(grant_user_id)
        except ValueError:
            return None
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(SandboxToolCallRecord).where(
                    SandboxToolCallRecord.run_id == rid,
                    SandboxToolCallRecord.step_id == step_id,
                    SandboxToolCallRecord.user_id == uid,
                ))
            if row is None:
                return None
            return {
                "result_status": row.result_status,
                "result_ciphertext": (
                    json.loads(self._cipher.decrypt(row.result_ciphertext))
                    if row.result_ciphertext else None
                ),
            }

    async def _store_tool_call_result(
        self, run_id: str, step_id: str, result: ToolResult
    ) -> None:
        """Update the reserved row with a finalized result (NEW #3).

        ALWAYS stores a non-NULL ciphertext — for successes with data AND for
        failures (the error becomes the ciphertext). This guarantees that a
        retried failed step returns the same failure, not fake success.
        """
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            return
        result_status = "succeeded" if result.success else "failed"
        payload = result.data if result.data is not None else {
            "error_code": result.error_code, "error_message": result.error_message,
        }
        result_ct = self._cipher.encrypt(
            json.dumps(payload, separators=(",", ":")))
        async with service_session(self._session_factory) as session:
            await session.execute(
                update(SandboxToolCallRecord)
                .where(SandboxToolCallRecord.run_id == rid,
                       SandboxToolCallRecord.step_id == step_id)
                .values(result_status=result_status, result_ciphertext=result_ct))

    # ---- Tool dispatch ------------------------------------------------

    async def _execute_tool(
        self, context: RequestContext, tool_id: str, input: dict[str, Any]
    ) -> ToolResult:
        if tool_id == "document.read":
            return await self._tool_document_read(context, input)
        elif tool_id == "document.create":
            return await self._tool_document_create(context, input)
        elif tool_id == "document.update":
            return await self._tool_document_update(context, input)
        elif tool_id == "document.delete":
            return await self._tool_document_delete(context, input)
        # ---- Task 4: MCP tool dispatch ---------------------------------
        elif self._mcp_registry is not None and self._mcp_executor is not None:
            return await self._execute_mcp_tool(context, tool_id, input)
        else:
            return ToolResult(success=False, error_code="SANDBOX_DENIED",
                              error_message=f"Unknown tool: {tool_id!r}")

    # ---- MCP tool dispatch (Task 4) ------------------------------------

    async def _execute_mcp_tool(
        self, context: RequestContext, tool_id: str, input: dict[str, Any],
    ) -> ToolResult:
        """Dispatch an MCP tool invocation: validate server/tool enabled,
        execute via the injectable executor, validate output schema.

        The approval re-check for mutable MCP tools already fires in
        ``invoke()`` before this method is reached (NEW #7).
        """
        # 1. Verify server + tool are enabled.
        registration = await self._mcp_registry.get_tool_registration(  # type: ignore[union-attr]
            context.user_id, tool_id,
        )
        if registration is None:
            return ToolResult(
                success=False, error_code="MCP_TOOL_DISABLED",
                error_message=f"MCP tool {tool_id!r} is not registered, "
                "enabled, or the server is disabled.",
            )

        # 2. Execute via the injectable MCP executor (real or fake).
        result = await self._mcp_executor.execute(tool_id, input)  # type: ignore[union-attr]

        # 3. Validate untrusted output against the registered schema.
        if result.success and self._mcp_validator is not None:
            output_schema = registration.get("output_schema", {})
            # Validate the raw untrusted output — default to {} only when the
            # executor returned something that isn't a dict (edge case).
            raw = result.data if isinstance(result.data, dict) else {}
            validated = self._mcp_validator.validate_output(output_schema, raw)
            if not validated.valid:
                return ToolResult(
                    success=False,
                    error_code=validated.error_code or "VALIDATION_FAILED",
                    error_message=validated.error_message or "Output validation failed.",
                )
            return ToolResult(success=True, data=validated.data)
        # Convert executor ToolResult → broker ToolResult (distinct types).
        return ToolResult(
            success=result.success,
            data=result.data,
            error_code=result.error_code,
            error_message=result.error_message,
        )

    async def _tool_document_read(self, context: RequestContext,
                                   input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        doc = await self._documents.get(context, document_id)
        if doc is None:
            return ToolResult(success=False, error_code="NOT_FOUND",
                              error_message="Document not found.")
        return ToolResult(success=True, data={
            "id": str(doc.id), "type": doc.type, "title": doc.title,
            "body": doc.body, "version": doc.version, "status": doc.status,
        })

    async def _tool_document_create(self, context: RequestContext,
                                     input: dict[str, Any]) -> ToolResult:
        doc_type = input.get("type")
        if not isinstance(doc_type, str) or doc_type not in (
            "memory", "interest", "task", "skill"):
            return ToolResult(success=False, error_code="VALIDATION_FAILED",
                              error_message="Invalid document type.")
        title = input.get("title", "")
        body = input.get("body", "")
        if not isinstance(title, str) or not isinstance(body, str):
            return ToolResult(success=False, error_code="VALIDATION_FAILED",
                              error_message="title and body must be strings.")
        doc = await self._documents.create_active(
            context, type=cast(DocumentType, doc_type), title=title, body=body)
        return ToolResult(success=True, data={
            "id": str(doc.id), "title": doc.title, "version": doc.version})

    async def _tool_document_update(self, context: RequestContext,
                                     input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        existing = await self._documents.get(context, document_id)
        if existing is None:
            return ToolResult(success=False, error_code="NOT_FOUND",
                              error_message="Document not found.")
        title = input.get("title", existing.title)
        body = input.get("body", existing.body)
        if not isinstance(title, str) or not isinstance(body, str):
            return ToolResult(success=False, error_code="VALIDATION_FAILED",
                              error_message="title and body must be strings.")
        doc = await self._documents.update_active(context, document_id,
                                                   title=title, body=body)
        return ToolResult(success=True, data={"id": str(doc.id), "version": doc.version})

    async def _tool_document_delete(self, context: RequestContext,
                                     input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        deleted = await self._documents.delete(context, document_id)
        if not deleted:
            return ToolResult(success=False, error_code="NOT_FOUND",
                              error_message="Document not found.")
        return ToolResult(success=True, data={"deleted": True})


def _require_uuid(input: dict[str, Any], key: str) -> uuid.UUID:
    value = input.get(key)
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", f"{key} must be a string.", False)
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", f"{key} must be a valid UUID.", False) from None
