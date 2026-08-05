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
from datetime import UTC, datetime
from typing import Any, cast

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import service_session, user_scoped_session
from app.models.chat import AgentRun
from app.models.chat import Message as MessageRecord
from app.models.chat import Session as SessionRecord
from app.models.chat import StreamEvent as StreamEventRecord
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    """A replayable run event with its decrypted, client-safe payload."""

    id: uuid.UUID
    run_id: uuid.UUID
    kind: str
    payload: str
    sequence: int
    created_at: datetime

    def public_payload(self) -> str:
        """The JSON string sent as the SSE ``data:`` line. Payloads are built server-side."""
        return self.payload


@dataclass(frozen=True)
class CreatedRun:
    """The durable handle for a newly queued agent run."""

    run_id: uuid.UUID
    session_id: uuid.UUID
    status: str
    created_at: datetime
    assistant_message_id: uuid.UUID
    user_message_id: uuid.UUID

    @classmethod
    def from_model(cls, run: AgentRun, assistant_message_id: uuid.UUID, user_message_id: uuid.UUID) -> CreatedRun:
        return cls(
            run_id=run.id,
            session_id=run.session_id,
            status=run.status,
            created_at=run.created_at,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
        )


@dataclass(frozen=True)
class ChatSession:
    """A chat session owned by one user, with its last activity time."""

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    last_message_at: datetime


@dataclass(frozen=True)
class RunState:
    """Read snapshot of an agent run plus its assistant message id."""

    run_id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    assistant_message_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


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

    async def create_session(
        self, context: RequestContext, title: str = "New chat"
    ) -> ChatSession:
        """Create a chat session owned by the caller (RLS-scoped)."""
        async with self._transaction(context) as session:
            record = SessionRecord(id=uuid.uuid4(), user_id=context.user_id, title=title)
            session.add(record)
            await session.flush()
            return ChatSession(
                id=record.id,
                user_id=record.user_id,
                title=record.title,
                created_at=record.created_at,
                last_message_at=record.updated_at,
            )

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
                return await self._created_run_from_messages(session, existing)
            next_sequence = (
                await session.scalar(
                    select(func.coalesce(func.max(MessageRecord.sequence), 0)).where(
                        MessageRecord.session_id == session_id
                    )
                )
            ) or 0
            run_id = uuid.uuid4()
            user_message = MessageRecord.user(
                context.user_id,
                session_id,
                self._cipher.encrypt(content),
                run_id=run_id,
                sequence=next_sequence + 1,
            )
            assistant_message = MessageRecord.assistant_placeholder(
                context.user_id,
                session_id,
                run_id,
                content_ciphertext=self._cipher.encrypt(""),
                sequence=next_sequence + 2,
            )
            run = AgentRun(
                id=run_id,
                user_id=context.user_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
                status="queued",
            )
            session.add_all([user_message, assistant_message, run])
            await session.flush()
            return CreatedRun(
                run_id=run.id,
                session_id=run.session_id,
                status=run.status,
                created_at=run.created_at,
                assistant_message_id=assistant_message.id,
                user_message_id=user_message.id,
            )

    @staticmethod
    async def _created_run_from_messages(
        session: AsyncSession, run: AgentRun
    ) -> CreatedRun:
        messages = (
            await session.scalars(select(MessageRecord).where(MessageRecord.run_id == run.id))
        ).all()
        return CreatedRun.from_model(
            run,
            assistant_message_id=next(m.id for m in messages if m.role == "assistant"),
            user_message_id=next(m.id for m in messages if m.role == "user"),
        )

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

    async def get_message(self, context: RequestContext, message_id: uuid.UUID) -> Message | None:
        """Return a message the caller owns, decrypted, or None (RLS-scoped)."""
        async with self._transaction(context) as session:
            row = await session.scalar(
                select(MessageRecord).where(MessageRecord.id == message_id)
            )
            if row is None:
                return None
            return Message(
                id=row.id,
                role=row.role,
                content=self._cipher.decrypt(row.content_ciphertext),
                status=row.status,
                session_id=row.session_id,
                run_id=row.run_id,
                sequence=row.sequence,
                created_at=row.created_at,
            )

    async def list_sessions(self, context: RequestContext) -> list[ChatSession]:
        """List the caller's sessions, most recently active first (RLS-scoped)."""
        async with self._transaction(context) as session:
            rows = (
                await session.scalars(
                    select(SessionRecord)
                    .where(SessionRecord.user_id == context.user_id)
                    .order_by(SessionRecord.updated_at.desc())
                )
            ).all()
            return [
                ChatSession(
                    id=row.id,
                    user_id=row.user_id,
                    title=row.title,
                    created_at=row.created_at,
                    last_message_at=row.updated_at,
                )
                for row in rows
            ]

    async def session_exists(self, context: RequestContext, session_id: uuid.UUID) -> bool:
        """True if the caller owns a session with ``session_id`` (RLS-scoped read).

        This is the write-side ownership check before creating a run: a user may only
        start a run in a session they own.
        """
        async with self._transaction(context) as session:
            count = await session.scalar(
                select(func.count()).select_from(SessionRecord).where(SessionRecord.id == session_id)
            )
            return (count or 0) > 0

    async def get_run(self, context: RequestContext, run_id: uuid.UUID) -> RunState | None:
        """Return the run's state if the caller owns it, else None (RLS-scoped read)."""
        async with self._transaction(context) as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
            if run is None:
                return None
            assistant = await session.scalar(
                select(MessageRecord).where(
                    MessageRecord.run_id == run_id, MessageRecord.role == "assistant"
                )
            )
            if assistant is None:
                raise ApiError("INTERNAL_ERROR", "Run has no assistant message.", False)
            return RunState(
                run_id=run.id,
                session_id=run.session_id,
                user_id=run.user_id,
                status=run.status,
                assistant_message_id=assistant.id,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )

    async def list_events(
        self, context: RequestContext, run_id: uuid.UUID, after: int
    ) -> list[StreamEvent]:
        """List the caller's stream events for a run with ``sequence > after`` (RLS-scoped)."""
        async with self._transaction(context) as session:
            rows = (
                await session.scalars(
                    select(StreamEventRecord)
                    .where(StreamEventRecord.run_id == run_id, StreamEventRecord.sequence > after)
                    .order_by(StreamEventRecord.sequence)
                )
            ).all()
            return [
                StreamEvent(
                    id=row.id,
                    run_id=run_id,
                    kind=row.kind,
                    payload=self._cipher.decrypt(row.payload_ciphertext),
                    sequence=row.sequence,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def count_stream_events(self, context: RequestContext, run_id: uuid.UUID, kind: str) -> int:
        """Count persisted events of ``kind`` for a run (used to resume generation)."""
        async with self._transaction(context) as session:
            count = await session.scalar(
                select(func.count())
                .select_from(StreamEventRecord)
                .where(StreamEventRecord.run_id == run_id, StreamEventRecord.kind == kind)
            )
            return count or 0

    async def update_run_status(self, context: RequestContext, run_id: uuid.UUID, status: str) -> None:
        """Set a run's status. ``updated_at`` is bumped explicitly (no DB trigger)."""
        async with self._transaction(context) as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
            if run is None:
                raise ApiError("NOT_FOUND", "Run not found.", False)
            run.status = status
            run.updated_at = _utcnow()
            await session.flush()

    async def claim_streaming(self, context: RequestContext, run_id: uuid.UUID) -> bool:
        """Atomically claim the right to generate for a queued run.

        A conditional ``queued -> streaming`` update returns rowcount 1 for exactly one
        caller; every overlapping same-key request observes rowcount 0 and must not start
        a second generation.
        """
        async with self._transaction(context) as session:
            result = cast(CursorResult[Any], await session.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.status == "queued")
                .values(status="streaming", updated_at=_utcnow())
            ))
            return result.rowcount == 1

    async def finalize_message(
        self, context: RequestContext, message_id: uuid.UUID, content: str, status: str = "completed"
    ) -> None:
        """Write the final assistant content and terminal status. Ciphertext only."""
        async with self._transaction(context) as session:
            message = await session.scalar(select(MessageRecord).where(MessageRecord.id == message_id))
            if message is None:
                raise ApiError("NOT_FOUND", "Message not found.", False)
            message.content_ciphertext = self._cipher.encrypt(content)
            message.status = status
            message.updated_at = _utcnow()
            await session.flush()

    async def purge_expired_events(self, older_than: datetime) -> int:
        """Delete stream events older than ``older_than`` for terminal runs only.

        This is the retention enforcement for replay events: an active run's events are
        never touched, so a reconnecting client can always resume a live stream.
        """
        async with self._service_session() as session:
            result = cast(CursorResult[Any], await session.execute(
                delete(StreamEventRecord).where(
                    StreamEventRecord.created_at < older_than,
                    StreamEventRecord.run_id.in_(
                        select(AgentRun.id).where(AgentRun.status.in_(("completed", "failed")))
                    ),
                )
            ))
            await session.flush()
            return result.rowcount if result.rowcount > 0 else 0

    async def delete_session(self, context: RequestContext, session_id: uuid.UUID) -> bool:
        """Hard-delete a session and its messages/runs/events (RLS-scoped to the owner).

        Returns True if a row was deleted, False if the session was not found or not
        owned by the caller. The chat tables carry no FKs (ownership is RLS), so the
        cascade is explicit: stream_events -> agent_runs -> messages -> session.
        """
        async with self._transaction(context) as session:
            row = await session.scalar(
                select(SessionRecord).where(SessionRecord.id == session_id)
            )
            if row is None:
                return False
            await session.execute(
                delete(StreamEventRecord).where(
                    StreamEventRecord.run_id.in_(
                        select(AgentRun.id).where(AgentRun.session_id == session_id)
                    )
                )
            )
            await session.execute(
                delete(AgentRun).where(AgentRun.session_id == session_id)
            )
            await session.execute(
                delete(MessageRecord).where(MessageRecord.session_id == session_id)
            )
            await session.delete(row)
            await session.flush()
            return True
