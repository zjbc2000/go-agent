"""Employee service: CRUD plus immutable HITL action approvals.

An employee is a lightweight agent persona (name/position/prompt). Fire/rehire/
adjust_position are never applied directly: they travel as a pending
``employee_approvals`` row and become effective only when the owner approves the
decision. The decision transaction is the atomic unit; for a chat-driven proposal
(``run_id`` set) the ``run.completed`` follow-on fires only after it commits.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from app.chat.service import ChatService
from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.repositories.employees import (
    Employee,
    EmployeeAction,
    EmployeeApproval,
    EmployeeDecision,
    EmployeeRepository,
)

_EMPLOYEE_APPROVAL_TTL = timedelta(minutes=15)

_VALID_ACTIONS: tuple[EmployeeAction, ...] = ("fire", "rehire", "adjust_position")
_VALID_DECISIONS: tuple[str, ...] = ("approve", "reject")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EmployeeService:
    """Creates employees and applies immutable fire/rehire/adjust approvals."""

    def __init__(
        self,
        repository: EmployeeRepository,
        cipher: EnvelopeCipher,
        chat: ChatService | None = None,
        approval_ttl: timedelta = _EMPLOYEE_APPROVAL_TTL,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._chat = chat
        self._approval_ttl = approval_ttl

    async def create_employee(self, context: RequestContext, name: str, position: str, prompt: str) -> Employee:
        """Create an active employee; name/position/prompt are all required."""
        if not isinstance(name, str) or not name.strip():
            raise ApiError("VALIDATION_FAILED", "name is required.", False)
        if not isinstance(position, str) or not position.strip():
            raise ApiError("VALIDATION_FAILED", "position is required.", False)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ApiError("VALIDATION_FAILED", "prompt is required.", False)
        return await self._repository.create(context, name.strip(), position.strip(), prompt.strip())

    async def update_employee(
        self,
        context: RequestContext,
        employee_id: UUID,
        *,
        name: str | None = None,
        position: str | None = None,
        prompt: str | None = None,
    ) -> Employee:
        """Update the caller's employee fields (at least one)."""
        fields = {"name": name, "position": position, "prompt": prompt}
        provided = {k: v for k, v in fields.items() if v is not None}
        if not provided:
            raise ApiError("VALIDATION_FAILED", "At least one of name, position, prompt is required.", False)
        for field, value in provided.items():
            if not isinstance(value, str):
                raise ApiError("VALIDATION_FAILED", f"{field} must be a string.", False)
            if not value.strip():
                raise ApiError("VALIDATION_FAILED", f"{field} must not be blank.", False)
        employee = await self._repository.update(
            context,
            employee_id,
            name=name.strip() if name is not None else None,
            position=position.strip() if position is not None else None,
            prompt=prompt.strip() if prompt is not None else None,
        )
        if employee is None:
            raise ApiError("NOT_FOUND", "Employee not found.", False)
        return employee

    async def list_employees(self, context: RequestContext) -> list[Employee]:
        """List the caller's employees (active and inactive)."""
        return await self._repository.list_all(context)

    async def create_employee_action_draft(
        self,
        context: RequestContext,
        *,
        employee_id: UUID,
        action: str,
        position: str | None = None,
        run_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> EmployeeApproval:
        """Create a pending fire/rehire/adjust approval with a canonical payload.

        Idempotent per ``run_id`` (chat-driven) or per explicit ``idempotency_key``
        (card-driven): a pending approval already recorded returns unchanged.
        """
        if action not in _VALID_ACTIONS:
            raise ApiError(
                "VALIDATION_FAILED",
                "action must be one of fire, rehire, adjust_position.",
                False,
            )
        if action == "adjust_position":
            if not isinstance(position, str) or not position.strip():
                raise ApiError("VALIDATION_FAILED", "position is required for adjust_position.", False)
        if run_id is not None:
            existing = await self._repository.get_pending_for_run(context, run_id)
            if existing is not None:
                return existing
        if idempotency_key is not None:
            existing = await self._repository.get_pending_for_key(context, employee_id, idempotency_key)
            if existing is not None:
                return existing
        employee = await self._repository.get(context, employee_id)
        if employee is None:
            raise ApiError("NOT_FOUND", "Employee not found.", False)

        typed: EmployeeAction = cast(EmployeeAction, action)
        payload = {
            "action": typed,
            "employee_id": str(employee_id),
            "position": position.strip() if position else None,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_ciphertext = self._cipher.encrypt(canonical)
        payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        position_ciphertext = self._cipher.encrypt(position.strip()) if position else None
        return await self._repository.create_action_draft(
            context,
            employee_id=employee_id,
            action=typed,
            payload_ciphertext=payload_ciphertext,
            payload_sha256=payload_sha256,
            position_ciphertext=position_ciphertext,
            run_id=run_id,
            idempotency_key=idempotency_key,
            expires_at=_utcnow() + self._approval_ttl,
        )

    async def decide_employee_approval(
        self,
        context: RequestContext,
        approval_id: UUID,
        decision: str,
        idempotency_key: str,
    ) -> EmployeeDecision:
        """Apply an immutable decision; the run follow-on fires after commit."""
        if decision not in _VALID_DECISIONS:
            raise ApiError("VALIDATION_FAILED", "decision must be one of approve, reject.", False)
        result = await self._repository.decide_action(context, approval_id, decision, idempotency_key)
        if result.run_id is not None and self._chat is not None:
            # Preserve the already-streamed explanation as the assistant message.
            await self._chat.complete_run_with_message(context, result.run_id, None)
        return result
