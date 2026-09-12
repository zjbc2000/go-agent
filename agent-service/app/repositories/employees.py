"""Encrypted company-employees repository.

All reads/writes run as the end-user JWT (RLS by ``user_id = auth.uid()``). Name,
position, and prompt are encrypted with the application envelope cipher before any
write and decrypted only after an authorized read — the same rule as planning
documents. Employees are single-row entities (last write wins, no versioning).

``EmployeeApproval`` rows are HITL decisions over fire/rehire/adjust_position. The
decision is one atomic user-scoped transaction: the approval row is locked
``FOR UPDATE``, the employee transition is applied, and the approval is resolved —
a repeated ``idempotency_key`` replays the original result, a different key after
resolution surfaces as ``APPROVAL_CONFLICT``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import user_scoped_session
from app.models.employees import Employee as EmployeeRecord
from app.models.employees import EmployeeApproval as EmployeeApprovalRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

EmployeeStatus = Literal["active", "inactive"]
EmployeeAction = Literal["fire", "rehire", "adjust_position"]

_VALID_ACTIONS: tuple[EmployeeAction, ...] = ("fire", "rehire", "adjust_position")


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Employee:
    """A decrypted employee with its lifecycle status."""

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    position: str
    prompt: str
    status: EmployeeStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EmployeeApproval:
    """A decrypted approval over an employee action (proposal-time snapshot)."""

    approval_id: uuid.UUID
    employee_id: uuid.UUID
    action: EmployeeAction
    name: str
    position: str | None
    status: EmployeeStatus
    position_to_set: str | None


@dataclass(frozen=True)
class EmployeeDecision:
    """The immutable outcome of an employee-action approval decision.

    ``employee`` is the refreshed employee on approve and None on reject (or when an
    idempotent replay resolves with no transition). ``run_id`` lets the service fire
    the ``run.completed`` follow-on after the decision transaction commits.
    """

    approval_id: uuid.UUID
    decision: str
    employee: Employee | None
    run_id: uuid.UUID | None


class EmployeeRepository:
    """Persistence boundary for encrypted company employees and their approvals."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    @asynccontextmanager
    async def _transaction(self, context: RequestContext) -> AsyncIterator[AsyncSession]:
        async with user_scoped_session(self._session_factory, context) as session:
            yield session

    def _to_employee(self, row: EmployeeRecord) -> Employee:
        return Employee(
            id=row.id,
            user_id=row.user_id,
            name=self._cipher.decrypt(row.name_ciphertext),
            position=self._cipher.decrypt(row.position_ciphertext),
            prompt=self._cipher.decrypt(row.prompt_ciphertext),
            status=cast(EmployeeStatus, row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _to_approval(self, approval: EmployeeApprovalRecord, employee: EmployeeRecord) -> EmployeeApproval:
        return EmployeeApproval(
            approval_id=approval.id,
            employee_id=approval.employee_id,
            action=cast(EmployeeAction, approval.action),
            name=self._cipher.decrypt(employee.name_ciphertext),
            position=self._cipher.decrypt(employee.position_ciphertext),
            status=cast(EmployeeStatus, employee.status),
            position_to_set=(
                self._cipher.decrypt(approval.position_ciphertext) if approval.position_ciphertext else None
            ),
        )

    async def create(self, context: RequestContext, name: str, position: str, prompt: str) -> Employee:
        """Create an employee with status active; fields are ciphertext-only."""
        async with self._transaction(context) as session:
            row = EmployeeRecord(
                id=uuid.uuid4(),
                user_id=context.user_id,
                status="active",
                name_ciphertext=self._cipher.encrypt(name),
                position_ciphertext=self._cipher.encrypt(position),
                prompt_ciphertext=self._cipher.encrypt(prompt),
            )
            session.add(row)
            await session.flush()
            return self._to_employee(row)

    async def list_all(self, context: RequestContext) -> list[Employee]:
        """List the caller's employees (active and inactive), newest first."""
        async with self._transaction(context) as session:
            rows = (
                await session.scalars(select(EmployeeRecord).order_by(EmployeeRecord.created_at.desc()))
            ).all()
            return [self._to_employee(row) for row in rows]

    async def list_active(self, context: RequestContext) -> list[Employee]:
        """List the caller's active employees, oldest first (stable context order)."""
        async with self._transaction(context) as session:
            rows = (
                await session.scalars(
                    select(EmployeeRecord).where(EmployeeRecord.status == "active").order_by(EmployeeRecord.created_at)
                )
            ).all()
            return [self._to_employee(row) for row in rows]

    async def get(self, context: RequestContext, employee_id: uuid.UUID) -> Employee | None:
        """Return the caller's employee, or None if not owned (RLS)."""
        async with self._transaction(context) as session:
            row = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == employee_id))
            return self._to_employee(row) if row is not None else None

    async def update(
        self,
        context: RequestContext,
        employee_id: uuid.UUID,
        *,
        name: str | None = None,
        position: str | None = None,
        prompt: str | None = None,
    ) -> Employee | None:
        """Update the caller's employee fields; None if not found (RLS)."""
        async with self._transaction(context) as session:
            row = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == employee_id))
            if row is None:
                return None
            if name is not None:
                row.name_ciphertext = self._cipher.encrypt(name)
            if position is not None:
                row.position_ciphertext = self._cipher.encrypt(position)
            if prompt is not None:
                row.prompt_ciphertext = self._cipher.encrypt(prompt)
            row.updated_at = _utcnow()
            await session.flush()
            return self._to_employee(row)

    async def set_status(
        self, context: RequestContext, employee_id: uuid.UUID, status: EmployeeStatus
    ) -> Employee | None:
        """Flip the caller's employee status (fire→inactive, rehire→active)."""
        async with self._transaction(context) as session:
            row = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == employee_id))
            if row is None:
                return None
            row.status = status
            row.updated_at = _utcnow()
            await session.flush()
            return self._to_employee(row)

    async def get_pending_for_run(self, context: RequestContext, run_id: uuid.UUID) -> EmployeeApproval | None:
        """Return the caller's pending approval linked to ``run_id`` (idempotency per run)."""
        async with self._transaction(context) as session:
            approval = await session.scalar(
                select(EmployeeApprovalRecord).where(
                    EmployeeApprovalRecord.run_id == run_id,
                    EmployeeApprovalRecord.status == "pending",
                )
            )
            if approval is None:
                return None
            employee = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == approval.employee_id))
            if employee is None:
                return None
            return self._to_approval(approval, employee)

    async def get_pending_for_key(
        self, context: RequestContext, employee_id: uuid.UUID, idempotency_key: str
    ) -> EmployeeApproval | None:
        """Return the caller's pending approval matching an explicit idempotency_key."""
        async with self._transaction(context) as session:
            approval = await session.scalar(
                select(EmployeeApprovalRecord).where(
                    EmployeeApprovalRecord.employee_id == employee_id,
                    EmployeeApprovalRecord.idempotency_key == idempotency_key,
                    EmployeeApprovalRecord.status == "pending",
                )
            )
            if approval is None:
                return None
            employee = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == approval.employee_id))
            if employee is None:
                return None
            return self._to_approval(approval, employee)

    async def create_action_draft(
        self,
        context: RequestContext,
        *,
        employee_id: uuid.UUID,
        action: EmployeeAction,
        payload_ciphertext: str,
        payload_sha256: str,
        position_ciphertext: str | None,
        run_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        expires_at: datetime,
    ) -> EmployeeApproval:
        """Insert a pending employee-action approval in one transaction."""
        async with self._transaction(context) as session:
            employee = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == employee_id))
            if employee is None:
                raise ApiError("NOT_FOUND", "Employee not found.", False)
            approval = EmployeeApprovalRecord(
                id=uuid.uuid4(),
                user_id=context.user_id,
                employee_id=employee_id,
                action=action,
                position_ciphertext=position_ciphertext,
                payload_ciphertext=payload_ciphertext,
                payload_sha256=payload_sha256,
                status="pending",
                run_id=run_id,
                idempotency_key=idempotency_key,
                expires_at=expires_at,
            )
            session.add(approval)
            await session.flush()
            return self._to_approval(approval, employee)

    async def decide_action(
        self,
        context: RequestContext,
        approval_id: uuid.UUID,
        decision: str,
        idempotency_key: str,
    ) -> EmployeeDecision:
        """Apply an immutable employee-action decision (atomic + idempotent).

        Approve applies the transition (fire→inactive, rehire→active,
        adjust_position→new position); reject only resolves the approval.
        """
        async with self._transaction(context) as session:
            approval = await session.scalar(
                select(EmployeeApprovalRecord).where(EmployeeApprovalRecord.id == approval_id).with_for_update()
            )
            if approval is None:
                raise ApiError("NOT_FOUND", "Approval not found.", False)
            if approval.status != "pending":
                if approval.idempotency_key == idempotency_key:
                    return await self._replay(session, approval)
                raise ApiError(
                    "APPROVAL_CONFLICT",
                    "Approval was already decided; use the original idempotency_key.",
                    False,
                )
            if approval.expires_at is not None and approval.expires_at <= _utcnow():
                raise ApiError("APPROVAL_EXPIRED", "Approval has expired.", False)
            employee = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == approval.employee_id))
            if employee is None:
                raise ApiError("NOT_FOUND", "Employee not found.", False)
            if decision == "approve":
                await self._apply_transition(employee, approval)
            else:
                approval.decision = "rejected"
            approval.status = approval.decision  # type: ignore[assignment]  # both confirmed/rejected literals
            approval.idempotency_key = idempotency_key
            approval.resolved_by = context.user_id
            approval.decided_at = _utcnow()
            approval.updated_at = _utcnow()
            await session.flush()
            return EmployeeDecision(
                approval_id=approval.id,
                decision=approval.decision or approval.status,
                employee=self._to_employee(employee) if decision == "approve" else None,
                run_id=approval.run_id,
            )

    async def _apply_transition(self, employee: EmployeeRecord, approval: EmployeeApprovalRecord) -> None:
        if approval.action == "fire":
            employee.status = "inactive"
        elif approval.action == "rehire":
            employee.status = "active"
        elif approval.action == "adjust_position":
            position = self._cipher.decrypt(approval.position_ciphertext or "")
            employee.position_ciphertext = self._cipher.encrypt(position)
        else:
            raise ApiError("VALIDATION_FAILED", f"Unknown employee action: {approval.action}.", False)
        approval.decision = "confirmed"
        employee.updated_at = _utcnow()

    async def _replay(self, session: AsyncSession, approval: EmployeeApprovalRecord) -> EmployeeDecision:
        employee = await session.scalar(select(EmployeeRecord).where(EmployeeRecord.id == approval.employee_id))
        return EmployeeDecision(
            approval_id=approval.id,
            decision=approval.decision or approval.status,
            employee=self._to_employee(employee) if approval.decision == "confirmed" and employee is not None else None,
            run_id=approval.run_id,
        )
