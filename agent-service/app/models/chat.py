"""SQLAlchemy ORM models for encrypted chat persistence.

Message bodies and stream-event payloads are stored only as KMS-envelope-encrypted
ciphertext; raw columns never contain plaintext. Row access is enforced by RLS
(``user_id = auth.uid()``); the models never embed secrets or plaintext content.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db.session import Base
from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Session(Base):
    """A chat session owned by one user."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class AgentRun(Base):
    """A queued/streaming/completed agent execution owned by one user."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="agent_runs_user_id_idempotency_key_key"),
        CheckConstraint(
            "status in ('queued', 'streaming', 'waiting_approval', 'completed', 'failed', 'cancelled')",
            name="agent_runs_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class Message(Base):
    """A user or assistant message; the body is stored only as ciphertext."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'system')", name="messages_role_check"),
        CheckConstraint(
            "status in ('queued', 'streaming', 'completed', 'failed')", name="messages_status_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    @classmethod
    def user(
        cls, user_id: uuid.UUID, session_id: uuid.UUID, content_ciphertext: str, *, run_id: uuid.UUID, sequence: int
    ) -> Message:
        return cls(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            role="user",
            content_ciphertext=content_ciphertext,
            status="completed",
            sequence=sequence,
        )

    @classmethod
    def assistant_placeholder(
        cls, user_id: uuid.UUID, session_id: uuid.UUID, run_id: uuid.UUID, *, content_ciphertext: str, sequence: int
    ) -> Message:
        return cls(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            role="assistant",
            content_ciphertext=content_ciphertext,
            status="streaming",
            sequence=sequence,
        )


class StreamEvent(Base):
    """A replayable, time-bounded delta for a run; the payload is stored only as ciphertext."""

    __tablename__ = "stream_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="stream_events_run_id_sequence_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
