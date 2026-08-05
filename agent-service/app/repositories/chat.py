"""Encrypted chat persistence repository.

``create_run`` and ``list_messages`` run as the end-user JWT (RLS); message bodies
are encrypted with the application envelope cipher before any write and decrypted
only after authorization. ``append_event`` is an agent-side write that runs
unscoped (service role) but records the owning run's ``user_id`` so reads remain
RLS-gated.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import service_session, user_scoped_session
from app.models.chat import AgentRun
from app.models.chat import Message as MessageRecord
from app.models.chat import StreamEvent as StreamEventRecord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class Message:
    """A decrypted chat message."""

    id: uuid.UUID
    role: str
    content: str
    status: str
    session_id: uuid.UUID
    run_id: uuid.UUID | None
    sequence: int
    created_at: datetime


@dataclass(frozen=True)
class StreamEvent:
    """A replayable run event with its plaintext payload."""

    id: uuid.UUID
    run_id: uuid.UUID
    kind: str
    payload: str
    sequence: int
    created_at: datetime


@dataclass(frozen=True)
class CreatedRun:
    """The durable handle for a newly queued agent run."""

    run_id: uuid.UUID
    session_id: uuid.UUID
    status: str
    created_at: datetime

    @classmethod
    def from_model(cls, run: AgentRun) -> CreatedRun:
        return cls(run_id=run.id, session_id=run.session_id, status=run.status, created_at=run.created_at)


class ChatRepository:
    """Persistence boundary for sessions, messages, agent runs, and stream events."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    @asynccontextmanager
    async def _transaction(self, context: RequestContext) -> AsyncIterator[AsyncSession]:
        async with user_scoped_session(self._session_factory, context) as session:
            yield session

    @asynccontextmanager
    async def _service_session(self) -> AsyncIterator[AsyncSession]:
        async with service_session(self._session_factory) as session:
            yield session

    async def create_run(
        self, context: RequestContext, session_id: uuid.UUID, content: str, idempotency_key: str
    ) -> CreatedRun:
        """Create a user message, an assistant placeholder, and a queued run in one transaction.

        Idempotent: a duplicate ``idempotency_key`` returns the original run.
        Content is encrypted before insert, so raw columns never hold plaintext.
        """
        async with self._transaction(context) as session:
            existing = await session.scalar(
                select(AgentRun).where(
                    AgentRun.user_id == context.user_id,
                    AgentRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return CreatedRun.from_model(existing)
            next_sequence = (
                await session.scalar(
                    select(func.coalesce(func.max(MessageRecord.sequence), 0)).where(
                        MessageRecord.session_id == session_id
                    )
                )
            ) or 0
            run_id = uuid.uuid4()
            run = AgentRun(
                id=run_id,
                user_id=context.user_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
                status="queued",
            )
            session.add_all(
                [
                    MessageRecord.user(
                        context.user_id,
                        session_id,
                        self._cipher.encrypt(content),
                        run_id=run_id,
                        sequence=next_sequence + 1,
                    ),
                    MessageRecord.assistant_placeholder(
                        context.user_id,
                        session_id,
                        run_id,
                        content_ciphertext=self._cipher.encrypt(""),
                        sequence=next_sequence + 2,
                    ),
                    run,
                ]
            )
            await session.flush()
            return CreatedRun.from_model(run)

    async def append_event(self, run_id: uuid.UUID, kind: str, payload: str) -> StreamEvent:
        """Append an encrypted stream event for a run, continuing its sequence."""
        async with self._service_session() as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
            if run is None:
                raise ApiError("NOT_FOUND", "Run not found.", False)
            next_sequence = (
                await session.scalar(
                    select(func.coalesce(func.max(StreamEventRecord.sequence), 0)).where(
                        StreamEventRecord.run_id == run_id
                    )
                )
            ) or 0
            event = StreamEventRecord(
                user_id=run.user_id,
                run_id=run_id,
                kind=kind,
                payload_ciphertext=self._cipher.encrypt(payload),
                sequence=next_sequence + 1,
            )
            session.add(event)
            await session.flush()
            return StreamEvent(
                id=event.id,
                run_id=run_id,
                kind=kind,
                payload=payload,
                sequence=event.sequence,
                created_at=event.created_at,
            )

    async def list_messages(self, context: RequestContext, session_id: uuid.UUID) -> list[Message]:
        """List the user's messages for a session, decrypted, in insertion order."""
        async with self._transaction(context) as session:
            rows = (
                await session.scalars(
                    select(MessageRecord)
                    .where(MessageRecord.session_id == session_id)
                    .order_by(MessageRecord.sequence, MessageRecord.created_at)
                )
            ).all()
            return [
                Message(
                    id=row.id,
                    role=row.role,
                    content=self._cipher.decrypt(row.content_ciphertext),
                    status=row.status,
                    session_id=row.session_id,
                    run_id=row.run_id,
                    sequence=row.sequence,
                    created_at=row.created_at,
                )
                for row in rows
            ]
