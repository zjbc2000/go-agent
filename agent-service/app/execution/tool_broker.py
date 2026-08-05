"""FastAPI tool broker: the sandbox container's ONLY external channel.

Every tool invocation from an isolated sandbox container passes through the
broker. It authenticates via a signed grant token (NOT the standard internal
token + JWT — the sandbox has only the grant), reloads the run and its approval
from PostgreSQL (so a leaked grant is still bounded by current DB state),
recompiles the plan from the pinned document version to verify the hash chain
and bind tool dispatch to the plan's declared step (not the caller-supplied
tool_id), executes the tool AS the grant's user_id via the user-scoped
repository, and records a redacted audit row.

Idempotent replay (TOCTOU-safe): the broker reserves the sandbox_tool_calls row
(INSERT ... ON CONFLICT DO NOTHING RETURNING id) BEFORE executing the side
effect. Only the reservation winner executes; the winner stores the encrypted
result payload in the row so a retried step can return the prior result.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

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

# Valid run statuses that permit tool execution through the broker.
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
    ) -> None:
        self._session_factory = session_factory
        self._documents = documents
        self._cipher = cipher
        self._grant_verifier = grant_verifier

    async def invoke(
        self, grant_token: str, step_id: str, tool_id: str, input: dict[str, Any]
    ) -> ToolResult:
        """Authenticate the grant, validate bounds, execute the tool, record audit.

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

        # 2. Reload the run from PostgreSQL (service role) so a leaked grant is
        #    still bounded by current DB state.
        run = await self._load_run(grant.run_id, grant.user_id)
        if run is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run not found.", False)
        if run["status"] not in _IN_FLIGHT_STATUSES:
            raise ApiError("SANDBOX_GRANT_INVALID", "Run is not in flight.", False)

        # 3. Verify the plan-hash chain (I2): recompile the plan from the pinned
        #    document version, verify it matches grant.plan_hash == run.plan_hash,
        #    then look up the step by step_id and execute THAT step's declared tool
        #    — the caller-supplied tool_id is IGNORED (or must equal the step's).
        plan = await self._recompile_and_verify(run, grant.plan_hash)
        step_tool = self._resolve_step_tool(plan, step_id, tool_id)

        # 4. For write/delete steps, verify the execution approval is confirmed
        #    and not expired (I1). Read-only steps have no approval.
        if step_tool in WRITE_TOOLS:
            await self._verify_approval(run)

        # 5. Build a user-scoped context for the grant's user_id.
        user_id = uuid.UUID(grant.user_id)
        context = RequestContext(user_id=user_id, role="user", request_id=grant.run_id)

        # 6. Idempotent replay (I3 TOCTOU fix): reserve the row BEFORE executing.
        #    INSERT ... ON CONFLICT DO NOTHING RETURNING id — only the winner gets
        #    a non-None id from RETURNING; the loser must load the stored result.
        run_id_str = str(run["id"])
        won = await self._reserve_tool_call(run_id_str, grant.user_id, step_id, step_tool)
        if not won:
            # Loser: load the stored result from the winner.
            stored_result = await self._load_tool_call_result(run_id_str, step_id)
            if stored_result is not None:
                return ToolResult(success=True, data=stored_result)
            return ToolResult(success=True, data={"replayed": True})

        # 7. Execute the tool as the grant's user, using the STEP'S declared tool.
        result = await self._execute_tool(context, step_tool, input)

        # 8. Store the result in the reserved row so replays can return it.
        await self._store_tool_call_result(run_id_str, step_id, result)

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
                "version_id": str(row.version_id),
                "plan_hash": row.plan_hash,
                "inputs_ciphertext": row.inputs_ciphertext,
                "approval_id": str(row.approval_id) if row.approval_id else None,
            }

    async def _verify_approval(self, run: dict[str, Any]) -> None:
        """Verify the execution approval for write/delete steps (I1).

        The approval must exist, be ``confirmed``, and not past ``expires_at``
        at call time. Read-only runs have no approval_id — the caller guards
        against that.
        """
        approval_id = run.get("approval_id")
        if not approval_id:
            raise ApiError("SANDBOX_GRANT_INVALID", "Write step requires an execution approval.", False)
        try:
            aid = uuid.UUID(approval_id)
        except ValueError:
            raise ApiError("SANDBOX_GRANT_INVALID", "Invalid approval reference.", False)
        async with service_session(self._session_factory) as session:
            approval = await session.scalar(
                select(ExecutionApprovalRecord).where(ExecutionApprovalRecord.id == aid)
            )
        if approval is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Execution approval not found.", False)
        if approval.status != "confirmed":
            raise ApiError("SANDBOX_GRANT_INVALID", "Execution approval was not confirmed.", False)
        now = _utcnow()
        if approval.expires_at is not None and approval.expires_at <= now:
            raise ApiError("SANDBOX_GRANT_INVALID", "Execution approval has expired.", False)

    async def _recompile_and_verify(
        self, run: dict[str, Any], grant_plan_hash: str
    ) -> ExecutionPlan:
        """Recompile the plan from the pinned document version and verify the hash chain (I2).

        The hash chain is: compile(document_version, inputs) -> plan.hash.
        Both run.plan_hash and grant.plan_hash must match the recompiled hash.
        """
        try:
            vid = uuid.UUID(run["version_id"])
            did = uuid.UUID(run["document_id"])
        except (ValueError, KeyError):
            raise ApiError("SANDBOX_GRANT_INVALID", "Run references invalid document version.", False)

        async with service_session(self._session_factory) as session:
            body_ct = await session.scalar(
                select(DocumentVersionRecord.body_ciphertext).where(
                    DocumentVersionRecord.id == vid,
                    DocumentVersionRecord.document_id == did,
                )
            )
        if body_ct is None:
            raise ApiError("SANDBOX_GRANT_INVALID", "Document version not found.", False)

        manifest = json.loads(self._cipher.decrypt(body_ct))
        inputs_payload = self._cipher.decrypt_json(run["inputs_ciphertext"])
        inputs = inputs_payload.get("inputs", {}) if isinstance(inputs_payload, dict) else {}

        plan = compile_skill(manifest, inputs, version_id=vid)

        if plan.hash != run["plan_hash"]:
            raise ApiError(
                "SANDBOX_GRANT_INVALID",
                "Plan hash mismatch: recompiled plan does not match the stored run hash.",
                False,
            )
        if plan.hash != grant_plan_hash:
            raise ApiError(
                "SANDBOX_GRANT_INVALID",
                "Plan hash mismatch: grant plan_hash does not match the recompiled plan.",
                False,
            )
        return plan

    def _resolve_step_tool(self, plan: ExecutionPlan, step_id: str, caller_tool_id: str) -> str:
        """Find the step by id in the recompiled plan; return its declared tool.

        The caller-supplied ``tool_id`` must match the step's declared tool, or
        the call is rejected — tool dispatch is determined by the plan, not the
        request (I2).
        """
        for step in plan.steps:
            if step.id == step_id:
                if step.tool != caller_tool_id:
                    raise ApiError(
                        "SANDBOX_GRANT_INVALID",
                        f"Step {step_id!r} tool mismatch: request says {caller_tool_id!r}, "
                        f"plan declares {step.tool!r}.",
                        False,
                    )
                return step.tool
        raise ApiError("SANDBOX_GRANT_INVALID", f"Step {step_id!r} not found in the plan.", False)

    async def _reserve_tool_call(
        self, run_id: str, user_id_str: str, step_id: str, tool_id: str
    ) -> bool:
        """Reserve the (run_id, step_id) row BEFORE executing (I3 TOCTOU fix).

        INSERT ... ON CONFLICT DO NOTHING RETURNING id.
        Returns True if this caller won the reservation (insert succeeded);
        False if the row already exists (caller is a retry and must load
        the stored result).
        """
        try:
            rid = uuid.UUID(run_id)
            uid = uuid.UUID(user_id_str)
        except ValueError:
            return False
        async with service_session(self._session_factory) as session:
            row = await session.execute(
                insert(SandboxToolCallRecord)
                .values(
                    run_id=rid,
                    user_id=uid,
                    step_id=step_id,
                    tool_id=tool_id,
                    result_status="reserved",
                )
                .on_conflict_do_nothing(index_elements=["run_id", "step_id"])
                .returning(SandboxToolCallRecord.id)
            )
            inserted = row.first()
            return inserted is not None

    async def _load_tool_call_result(
        self, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        """Return the decrypted stored result for a prior tool call, or None."""
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
            if row is None or row.result_ciphertext is None:
                return None
            return json.loads(self._cipher.decrypt(row.result_ciphertext))

    async def _store_tool_call_result(
        self, run_id: str, step_id: str, result: ToolResult
    ) -> None:
        """Update the reserved row with the execution result."""
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            return
        result_status = "succeeded" if result.success else "failed"
        result_ct: str | None = None
        if result.data is not None:
            result_ct = self._cipher.encrypt(json.dumps(result.data, separators=(",", ":")))
        async with service_session(self._session_factory) as session:
            await session.execute(
                update(SandboxToolCallRecord)
                .where(
                    SandboxToolCallRecord.run_id == rid,
                    SandboxToolCallRecord.step_id == step_id,
                )
                .values(result_status=result_status, result_ciphertext=result_ct)
            )

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
            return ToolResult(
                success=False, error_code="SANDBOX_DENIED",
                error_message=f"Unknown tool: {tool_id!r}",
            )

    async def _tool_document_read(self, context: RequestContext, input: dict[str, Any]) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        doc = await self._documents.get(context, document_id)
        if doc is None:
            return ToolResult(success=False, error_code="NOT_FOUND", error_message="Document not found.")
        return ToolResult(success=True, data={
            "id": str(doc.id), "type": doc.type, "title": doc.title, "body": doc.body,
            "version": doc.version, "status": doc.status,
        })

    async def _tool_document_create(
        self, context: RequestContext, input: dict[str, Any]
    ) -> ToolResult:
        doc_type = input.get("type")
        if not isinstance(doc_type, str) or doc_type not in (
            "memory", "interest", "task", "skill",
        ):
            return ToolResult(
                success=False, error_code="VALIDATION_FAILED",
                error_message="Invalid document type.",
            )
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
        return ToolResult(success=True, data={
            "id": str(doc.id), "title": doc.title, "version": doc.version,
        })

    async def _tool_document_update(
        self, context: RequestContext, input: dict[str, Any]
    ) -> ToolResult:
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

    async def _tool_document_delete(
        self, context: RequestContext, input: dict[str, Any]
    ) -> ToolResult:
        document_id = _require_uuid(input, "document_id")
        deleted = await self._documents.delete(context, document_id)
        if not deleted:
            return ToolResult(success=False, error_code="NOT_FOUND", error_message="Document not found.")
        return ToolResult(success=True, data={"deleted": True})


def _require_uuid(input: dict[str, Any], key: str) -> uuid.UUID:
    value = input.get(key)
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", f"{key} must be a string.", False)
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", f"{key} must be a valid UUID.", False) from None
