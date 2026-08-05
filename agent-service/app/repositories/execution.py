"""User-scoped skill-execution repository: approvals and sandbox runs.

Every write runs as the end-user JWT (RLS by ``user_id = auth.uid()``); there is
no unscoped read path for execution content in this MVP. Execution inputs are
encrypted with the application envelope cipher before any write and only ever
stored as ciphertext. ``plan_hash`` pins each row to the exact immutable plan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import user_scoped_session
from app.execution.outbox import SANDBOX_EXECUTE_TASK, sandbox_execute_payload
from app.models.execution import ExecutionApproval as ExecutionApprovalRecord
from app.models.execution import OutboxEvent as OutboxEventRecord
from app.models.execution import SandboxRun as SandboxRunRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ExecutionApproval:
    """A created execution approval awaiting a decision."""

    id: uuid.UUID
    status: str
    plan_hash: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class SandboxRun:
    """A queued sandbox run of an immutable plan."""

    id: uuid.UUID
    status: str
    plan_hash: str
    created_at: datetime


class ExecutionRepository:
    """Persistence boundary for execution approvals and sandbox runs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    async def get_approval_by_idempotency_key(
        self, context: RequestContext, document_id: uuid.UUID, idempotency_key: str
    ) -> ExecutionApproval | None:
        """Return the caller's approval for a request key, or None (RLS)."""
        async with user_scoped_session(self._session_factory, context) as session:
            row = await session.scalar(
                select(ExecutionApprovalRecord).where(
                    ExecutionApprovalRecord.document_id == document_id,
                    ExecutionApprovalRecord.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            return self._to_approval(row)

    async def create_execution_approval(
        self,
        context: RequestContext,
        *,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        plan_hash: str,
        inputs_ciphertext: str,
        idempotency_key: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> ExecutionApproval:
        """Insert a pending execution approval in one user-scoped transaction."""
        async with user_scoped_session(self._session_factory, context) as session:
            row = ExecutionApprovalRecord(
                id=uuid.uuid4(),
                user_id=context.user_id,
                document_id=document_id,
                version_id=version_id,
                plan_hash=plan_hash,
                inputs_ciphertext=inputs_ciphertext,
                status="pending",
                idempotency_key=idempotency_key,
                expires_at=expires_at,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            await session.flush()
            return self._to_approval(row)

    async def get_run_by_idempotency_key(
        self, context: RequestContext, document_id: uuid.UUID, idempotency_key: str
    ) -> SandboxRun | None:
        """Return the caller's run for a request key, or None (RLS)."""
        async with user_scoped_session(self._session_factory, context) as session:
            row = await session.scalar(
                select(SandboxRunRecord).where(
                    SandboxRunRecord.document_id == document_id,
                    SandboxRunRecord.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            return self._to_run(row)

    async def create_sandbox_run(
        self,
        context: RequestContext,
        *,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        plan_hash: str,
        inputs_ciphertext: str,
        idempotency_key: str,
        created_at: datetime,
    ) -> SandboxRun:
        """Insert a queued sandbox run + its outbox event in ONE user-scoped transaction."""
        async with user_scoped_session(self._session_factory, context) as session:
            row = SandboxRunRecord(
                id=uuid.uuid4(),
                user_id=context.user_id,
                document_id=document_id,
                version_id=version_id,
                plan_hash=plan_hash,
                status="queued",
                inputs_ciphertext=inputs_ciphertext,
                idempotency_key=idempotency_key,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            self._add_sandbox_outbox_event(session, context.user_id, row.id, created_at=created_at)
            await session.flush()
            return self._to_run(row)

    async def confirm_execution(
        self,
        context: RequestContext,
        approval_id: uuid.UUID,
        idempotency_key: str,
        *,
        created_at: datetime,
    ) -> SandboxRun:
        """Resolve a pending approval and queue its run in one user-scoped transaction.

        Locks the approval row so concurrent confirms serialize: the first confirm
        resolves it and inserts the run + outbox event; a later confirm with the same
        ``idempotency_key`` returns that same run (idempotent) and a different key
        surfaces as APPROVAL_CONFLICT. An expired approval is APPROVAL_EXPIRED and a
        resolution never fires Celery for a run the caller cannot see.
        """
        async with user_scoped_session(self._session_factory, context) as session:
            approval = await session.scalar(
                select(ExecutionApprovalRecord)
                .where(ExecutionApprovalRecord.id == approval_id)
                .with_for_update()
            )
            if approval is None:
                raise ApiError("NOT_FOUND", "Execution approval not found.", False)
            if approval.status == "pending":
                if approval.expires_at is not None and approval.expires_at <= created_at:
                    raise ApiError("APPROVAL_EXPIRED", "Execution approval has expired.", True)
                approval.status = "confirmed"
                approval.decision = "confirmed"
                approval.resolved_by = context.user_id
                approval.decided_at = created_at
                approval.updated_at = created_at
                run = SandboxRunRecord(
                    id=uuid.uuid4(),
                    user_id=context.user_id,
                    document_id=approval.document_id,
                    version_id=approval.version_id,
                    plan_hash=approval.plan_hash,
                    status="queued",
                    approval_id=approval.id,
                    inputs_ciphertext=approval.inputs_ciphertext,
                    idempotency_key=idempotency_key,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(run)
                self._add_sandbox_outbox_event(session, context.user_id, run.id, created_at=created_at)
                await session.flush()
                return self._to_run(run)
            resolved = await session.scalar(
                select(SandboxRunRecord).where(SandboxRunRecord.approval_id == approval_id)
            )
            if resolved is not None and resolved.idempotency_key == idempotency_key:
                return self._to_run(resolved)
            raise ApiError(
                "APPROVAL_CONFLICT", "Execution approval was already resolved.", False
            )

    def _add_sandbox_outbox_event(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        created_at: datetime,
    ) -> None:
        """Insert the run's sandbox.execute outbox row in the current transaction."""
        session.add(
            OutboxEventRecord(
                user_id=user_id,
                event_type=SANDBOX_EXECUTE_TASK,
                aggregate_id=run_id,
                payload=sandbox_execute_payload(run_id),
                status="pending",
                attempts=0,
                created_at=created_at,
            )
        )

    def _to_approval(self, row: ExecutionApprovalRecord) -> ExecutionApproval:
        # Execution approvals are always created with a non-null expiry.
        return ExecutionApproval(
            id=row.id,
            status=row.status,
            plan_hash=row.plan_hash,
            expires_at=cast(datetime, row.expires_at),
            created_at=row.created_at,
        )

    def _to_run(self, row: SandboxRunRecord) -> SandboxRun:
        return SandboxRun(
            id=row.id,
            status=row.status,
            plan_hash=row.plan_hash,
            created_at=row.created_at,
        )
