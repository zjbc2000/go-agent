"""SQLAlchemy ORM models for company employees and their action approvals.

An employee is a lightweight agent persona: name/position/prompt stored only as
KMS-envelope-encrypted ciphertext (raw columns never contain plaintext), plus a
lifecycle ``status`` flag (``active`` = 在职, ``inactive`` = 已离职). Row access is
enforced by RLS (``user_id = auth.uid()``), mirroring planning documents.

``EmployeeApproval`` is a HITL decision over a fire/rehire/adjust_position action.
It mirrors the ``execution_approvals`` decision pattern (payload ciphertext + SHA-256
integrity hash, idempotency_key, expires_at, resolved_by) but stays independent of
planning's document-coupled approvals table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db.session import Base
from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Employee(Base):
    """An agent employee; current name/position/prompt live here, ciphertext-only."""

    __tablename__ = "employees"
    __table_args__ = (CheckConstraint("status in ('active', 'inactive')", name="employees_status_check"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    name_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    position_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EmployeeApproval(Base):
    """A pending/decided HITL approval over a fire/rehire/adjust_position action.

    ``payload_ciphertext`` is the canonical ``{"action","employee_id","position"}``
    payload, ``payload_sha256`` its integrity hash. ``position_ciphertext`` is the
    target position for an adjust_position (NULL otherwise). ``run_id`` links a
    chat-driven proposal to its run for the ``run.completed`` follow-on.
    """

    __tablename__ = "employee_approvals"
    __table_args__ = (
        CheckConstraint(
            "action in ('fire', 'rehire', 'adjust_position')",
            name="employee_approvals_action_check",
        ),
        CheckConstraint(
            "status in ('pending', 'confirmed', 'rejected')",
            name="employee_approvals_status_check",
        ),
        Index("employee_approvals_id_idempotency_key_idx", "id", "idempotency_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    position_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
