"""Skill execution service: compile a skill document into an immutable plan and
route the execution to an approval (write/delete steps) or a direct sandbox run.

A skill IS a planning document of category ``skill``; its body is the JSON
manifest. ``request_execution`` loads the document user-scoped (RLS), parses and
compiles the manifest, and records either an expiring ``execution_approvals``
row or a queued ``sandbox_runs`` row. A repeated ``idempotency_key`` returns the
original approval/run with no duplicate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.repositories.execution import ExecutionApproval, ExecutionRepository, SandboxRun
from app.repositories.planning import DocumentRepository
from app.skills.compiler import WRITE_TOOLS, canonical_json, compile_skill
from app.skills.schemas import ExecutionPlan, ExecutionRequestResult

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
    ) -> None:
        self._documents = documents
        self._execution = execution
        self._cipher = cipher

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
        plan = self._compile_manifest(document.body, document.current_version_id, inputs)
        if not plan.steps:
            raise ApiError("SKILL_INVALID", "A skill must have at least one step.", False)
        has_write = any(step.tool in WRITE_TOOLS for step in plan.steps)
        if has_write:
            approval = await self._request_approval(
                context, document_id, document.current_version_id, plan, inputs, idempotency_key
            )
            return ExecutionRequestResult(approval=approval, run=None)
        run = await self._request_run(
            context, document_id, document.current_version_id, plan, inputs, idempotency_key
        )
        return ExecutionRequestResult(approval=None, run=run)

    def _compile_manifest(self, body: str, version_id: UUID, inputs: dict[str, Any]) -> ExecutionPlan:
        try:
            manifest = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            raise ApiError("SKILL_INVALID", "Skill manifest is not valid JSON.", False) from None
        if not isinstance(manifest, dict):
            raise ApiError("SKILL_INVALID", "Skill manifest must be an object.", False)
        return compile_skill(manifest, inputs, version_id=version_id)

    async def _request_approval(
        self,
        context: RequestContext,
        document_id: UUID,
        version_id: UUID,
        plan: ExecutionPlan,
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionApproval:
        existing = await self._execution.get_approval_by_idempotency_key(
            context, document_id, idempotency_key
        )
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
        existing = await self._execution.get_run_by_idempotency_key(
            context, document_id, idempotency_key
        )
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
