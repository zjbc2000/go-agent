"""SQLAlchemy ORM models for versioned planning documents.

Formal planning text (title/body) is stored only as KMS-envelope-encrypted
ciphertext; raw columns never contain plaintext. Row access is enforced by RLS
(``user_id = auth.uid()``); the models never embed secrets or plaintext content.
``document_drafts``/``approvals``/``audit_logs`` tables exist in the migration but
their behavior is filled by Task 2, so they have no ORM models yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db.session import Base
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Document(Base):
    """An active planning document; current title/body live here and in a version row."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("type in ('memory', 'interest', 'task', 'skill')", name="documents_type_check"),
        CheckConstraint("status = 'active'", name="documents_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    title_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    body_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class DocumentVersion(Base):
    """An immutable version of a document; every versioned write appends one."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="documents_version_idx"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    body_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
