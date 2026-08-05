"""SQLAlchemy ORM models for skill execution: approvals, sandbox runs, and the outbox.

Skill execution inputs are stored only as KMS-envelope-encrypted ciphertext; raw
columns never contain plaintext. Row access is enforced by RLS
(``user_id = auth.uid()``); the models never embed secrets or plaintext content.
``plan_hash`` pins each row to the exact immutable plan (a specific
``document_version``) it executes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db.session import Base
from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutionApproval(Base):
    """An expiring approval to execute a write/delete skill plan."""

    __tablename__ = "execution_approvals"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'confirmed', 'rejected', 'superseded')",
            name="execution_approvals_status_check",
        ),
        Index(
            "execution_approvals_document_id_idempotency_key_idx",
            "document_id",
            "idempotency_key",
            unique=True,
        ),
        Index("execution_approvals_version_id_idx", "version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class SandboxRun(Base):
    """A queued execution of an immutable plan (read-only queues immediately)."""

    __tablename__ = "sandbox_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed', 'timed_out', 'policy_denied')",
            name="sandbox_runs_status_check",
        ),
        Index(
            "sandbox_runs_document_id_idempotency_key_idx",
            "document_id",
            "idempotency_key",
            unique=True,
        ),
        Index("sandbox_runs_version_id_idx", "version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    inputs_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class OutboxEvent(Base):
    """A transactional outbox row the service publishes on the user's behalf (Task 2)."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'published')", name="outbox_events_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class SandboxToolCall(Base):
    """A redacted audit trail of tool invocations from sandbox containers (Task 3).

    UNIQUE(run_id, step_id) makes every side effect idempotent by run+step —
    a retried step never re-executes. Raw input content is never stored.
    """

    __tablename__ = "sandbox_tool_calls"
    __table_args__ = (
        Index("sandbox_tool_calls_run_id_idx", "run_id"),
        Index("sandbox_tool_calls_user_id_idx", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_id: Mapped[str] = mapped_column(Text, nullable=False)
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    result_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class McpServer(Base):
    """A user-registered MCP server (Task 4 registry; no behavior yet)."""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    image: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    sbom: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class McpTool(Base):
    """A tool exposed by a user's MCP server (Task 4 registry; no behavior yet)."""

    __tablename__ = "mcp_tools"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    tool_id: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
