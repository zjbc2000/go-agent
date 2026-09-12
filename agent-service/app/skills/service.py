"""Skill execution service: compile a skill document into an immutable plan and
route the execution to an approval (write/delete steps) or a direct sandbox run.

A skill IS a planning document of category ``skill``; its body is the JSON
manifest. ``request_execution`` loads the document user-scoped (RLS), parses and
compiles the manifest, and records either an expiring ``execution_approvals``
row or a queued ``sandbox_runs`` row. A repeated ``idempotency_key`` returns the
original approval/run with no duplicate.

Task 4: the service consults ``McpRegistry`` to gate ``allowed_tools`` entries —
each must be a user-registered AND enabled MCP tool (else SKILL_INVALID). It
also expands the write-check to cover MCP tools marked ``mutable`` (NEW #7).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.repositories.execution import ExecutionApproval, ExecutionRepository, SandboxRun
from app.repositories.planning import DocumentRepository
from app.skills.compiler import WRITE_TOOLS, canonical_json, compile_skill
from app.skills.schemas import ExecutionPlan, ExecutionRequestResult

if TYPE_CHECKING:
    from app.mcp.registry import McpRegistry

_EXECUTION_APPROVAL_TTL = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SkillService:
    """Routes skill executions to approvals (writes) or queued runs (reads)."""

    def __init__(
        self,
        documents: DocumentRepository,
        execution: ExecutionRepository,
        cipher: EnvelopeCipher,
        mcp_registry: McpRegistry | None = None,
    ) -> None:
        self._documents = documents
        self._execution = execution
        self._cipher = cipher
        self._mcp = mcp_registry

    async def request_execution(
        self,
        context: RequestContext,
        document_id: UUID,
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionRequestResult:
        """Compile the skill document and record an approval or a queued run."""
        if not isinstance(inputs, dict):
            raise ApiError("VALIDATION_FAILED", "inputs must be an object.", False)
        document = await self._documents.get(context, document_id)
        if document is None:
            raise ApiError("NOT_FOUND", "Document not found.", False)
        if document.type != "skill":
            raise ApiError("SKILL_INVALID", "Document is not a skill.", False)

        # Task 4: validate allowed_tools entries against the live MCP registry.
        manifest = self._parse_manifest_json(document.body)
        await self._validate_allowed_tools(context, manifest)

        plan = self._compile_manifest_from_dict(manifest, document.current_version_id, inputs)
        if not plan.steps:
            raise ApiError("SKILL_INVALID", "A skill must have at least one step.", False)

        has_write = await self._has_write_step(context, plan.steps)
        if has_write:
            approval = await self._request_approval(
                context, document_id, document.current_version_id, plan, inputs, idempotency_key
            )
            return ExecutionRequestResult(approval=approval, run=None)
        run = await self._request_run(context, document_id, document.current_version_id, plan, inputs, idempotency_key)
        return ExecutionRequestResult(approval=None, run=run)

    async def confirm_execution(self, context: RequestContext, approval_id: UUID, idempotency_key: str) -> SandboxRun:
        """Confirm a pending approval, queue its run, and emit its outbox event.

        The whole confirmation is one user-scoped transaction: the approval is
        locked FOR UPDATE, resolved (expired -> APPROVAL_EXPIRED), and the run +
        ``sandbox.execute`` outbox row are inserted together so Celery can never
        learn of a run that was rolled back. Idempotent under a repeated
        ``idempotency_key`` (same run), conflict on a different key.
        """
        return await self._execution.confirm_execution(context, approval_id, idempotency_key, created_at=_utcnow())

    def _compile_manifest(self, body: str, version_id: UUID, inputs: dict[str, Any]) -> ExecutionPlan:
        try:
            manifest = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            raise ApiError("SKILL_INVALID", "Skill manifest is not valid JSON.", False) from None
        if not isinstance(manifest, dict):
            raise ApiError("SKILL_INVALID", "Skill manifest must be an object.", False)
        return compile_skill(manifest, inputs, version_id=version_id)

    @staticmethod
    def _parse_manifest_json(body: str) -> dict[str, Any]:
        try:
            manifest = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            raise ApiError("SKILL_INVALID", "Skill manifest is not valid JSON.", False) from None
        if not isinstance(manifest, dict):
            raise ApiError("SKILL_INVALID", "Skill manifest must be an object.", False)
        return manifest

    def _compile_manifest_from_dict(
        self,
        manifest: dict[str, Any],
        version_id: UUID,
        inputs: dict[str, Any],
    ) -> ExecutionPlan:
        return compile_skill(manifest, inputs, version_id=version_id)

    async def _validate_allowed_tools(
        self,
        context: RequestContext,
        manifest: dict[str, Any],
    ) -> None:
        """Gate: each allowed_tools entry must be a user-registered + enabled MCP tool."""
        allowed = manifest.get("allowed_tools", [])
        if not isinstance(allowed, list):
            raise ApiError("SKILL_INVALID", "allowed_tools must be a list.", False)
        if not allowed:
            return
        if self._mcp is None:
            raise ApiError(
                "SKILL_INVALID",
                "Manifest declares allowed_tools but MCP registry is not configured.",
                False,
            )
        for tool_id in allowed:
            if not isinstance(tool_id, str):
                raise ApiError("SKILL_INVALID", "Each allowed_tools entry must be a string.", False)
            if not await self._mcp.is_tool_allowed(context, tool_id):
                raise ApiError(
                    "SKILL_INVALID",
                    f"allowed_tools entry {tool_id!r} is not a registered and enabled MCP tool.",
                    False,
                )

    async def _has_write_step(
        self,
        context: RequestContext,
        steps: list[Any],
    ) -> bool:
        """True when any step requires an execution approval.

        Document writes + mutable MCP tools both gate on approval (NEW #7).
        """
        for step in steps:
            if step.tool in WRITE_TOOLS:
                return True
        if self._mcp is not None:
            for step in steps:
                if step.tool not in WRITE_TOOLS and not step.tool.startswith("document."):
                    if await self._mcp.is_tool_mutable(context.user_id, step.tool):
                        return True
        return False

    async def _request_approval(
        self,
        context: RequestContext,
        document_id: UUID,
        version_id: UUID,
        plan: ExecutionPlan,
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionApproval:
        existing = await self._execution.get_approval_by_idempotency_key(context, document_id, idempotency_key)
        if existing is not None:
            return existing
        now = _utcnow()
        payload = canonical_json({"plan_hash": plan.hash, "inputs": inputs})
        return await self._execution.create_execution_approval(
            context,
            document_id=document_id,
            version_id=version_id,
            plan_hash=plan.hash,
            inputs_ciphertext=self._cipher.encrypt(payload),
            idempotency_key=idempotency_key,
            expires_at=now + _EXECUTION_APPROVAL_TTL,
            created_at=now,
        )

    async def _request_run(
        self,
        context: RequestContext,
        document_id: UUID,
        version_id: UUID,
        plan: ExecutionPlan,
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> SandboxRun:
        existing = await self._execution.get_run_by_idempotency_key(context, document_id, idempotency_key)
        if existing is not None:
            return existing
        now = _utcnow()
        payload = canonical_json({"inputs": inputs})
        return await self._execution.create_sandbox_run(
            context,
            document_id=document_id,
            version_id=version_id,
            plan_hash=plan.hash,
            inputs_ciphertext=self._cipher.encrypt(payload),
            idempotency_key=idempotency_key,
            created_at=now,
        )
