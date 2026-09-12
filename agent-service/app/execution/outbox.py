"""Transactional outbox for sandbox scheduling.

A confirm/queue transaction writes the ``sandbox_runs`` row and its
``outbox_events`` row in the same user-scoped commit, so the worker can never
learn of a run that was rolled back. The event payload carries ONLY the
``sandbox_run_id`` (plan Global Constraint: all side effects are idempotent by
``(sandbox_run_id, step_id)`` and Celery needs nothing else to claim a run).

``OutboxRepository`` gives the publisher and the tests a read handle on pending
events; ``publish_pending_outbox_events`` (publisher.py) drains them to RabbitMQ
and marks them published only after the broker acknowledges.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import service_session
from app.models.execution import OutboxEvent as OutboxEventRecord

# The RabbitMQ queue and Celery task name for sandbox execution. Durable and
# persistent; the publisher routes and Celery consumes from this same name.
SANDBOX_EXECUTE_QUEUE = "sandbox.execute"
SANDBOX_EXECUTE_TASK = "sandbox.execute"

OUTBOX_PENDING = "pending"
OUTBOX_PUBLISHED = "published"


def sandbox_execute_payload(sandbox_run_id: uuid.UUID) -> dict[str, str]:
    """The ONLY payload the plan allows on a sandbox.execute event."""
    return {"sandbox_run_id": str(sandbox_run_id)}


class OutboxRepository:
    """Read/update handle over the outbox, acting as the system (service role)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def has_event(self, event_type: str, aggregate_id: uuid.UUID) -> bool:
        """True when an outbox row exists for ``(event_type, aggregate_id)``."""
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(OutboxEventRecord).where(
                    OutboxEventRecord.event_type == event_type,
                    OutboxEventRecord.aggregate_id == aggregate_id,
                )
            )
            return row is not None

    async def event_status(self, event_type: str, aggregate_id: uuid.UUID) -> str | None:
        """The status of a matching outbox row, or None if it does not exist."""
        async with service_session(self._session_factory) as session:
            row = await session.scalar(
                select(OutboxEventRecord.status).where(
                    OutboxEventRecord.event_type == event_type,
                    OutboxEventRecord.aggregate_id == aggregate_id,
                )
            )
            return row

    async def list_pending(self, *, max_attempts: int, now: datetime, limit: int = 100) -> list[dict[str, Any]]:
        """Return pending events eligible for publication, oldest first.

        An event is eligible when it has not exhausted its attempts and its
        ``next_attempt_at`` is due (null means immediately eligible).
        """
        async with service_session(self._session_factory) as session:
            rows = (
                await session.scalars(
                    select(OutboxEventRecord)
                    .where(
                        OutboxEventRecord.status == OUTBOX_PENDING,
                        OutboxEventRecord.attempts < max_attempts,
                        (OutboxEventRecord.next_attempt_at.is_(None)) | (OutboxEventRecord.next_attempt_at <= now),
                    )
                    .order_by(OutboxEventRecord.created_at.asc())
                    .limit(limit)
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "sandbox_run_id": row.payload.get("sandbox_run_id"),
                    "attempts": row.attempts,
                }
                for row in rows
            ]

    async def mark_published(self, event_id: uuid.UUID, *, published_at: datetime) -> None:
        """Mark an event published only after the broker acknowledged it."""
        async with service_session(self._session_factory) as session:
            row = await session.get(OutboxEventRecord, event_id)
            if row is None:
                return
            row.status = OUTBOX_PUBLISHED
            row.published_at = published_at

    async def record_failure(self, event_id: uuid.UUID, *, attempts: int, next_attempt_at: datetime) -> None:
        """Record a failed publish and schedule the next bounded-backoff attempt."""
        async with service_session(self._session_factory) as session:
            row = await session.get(OutboxEventRecord, event_id)
            if row is None:
                return
            row.attempts = attempts
            row.next_attempt_at = next_attempt_at
