"""SQLAlchemy ORM models for versioned planning documents and approvals.

Formal planning text (title/body) is stored only as KMS-envelope-encrypted
ciphertext; raw columns never contain plaintext. Row access is enforced by RLS
(``user_id = auth.uid()``); the models never embed secrets or plaintext content.
Approvals additionally hold a canonical encrypted payload (``payload_ciphertext``)
with its SHA-256 integrity hash; a pending draft is not a formal document.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db.session import Base
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, Uuid
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


class DocumentDraft(Base):
    """A pending proposal that either edits a document or proposes a new one.

    ``document_id`` is NULL when the draft proposes a brand-new document; approving
    it CREATES the document. ``superseded_at`` marks a draft sent back for
    regeneration. Ciphertext-only, like every formal planning row.
    """

    __tablename__ = "document_drafts"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'confirmed', 'rejected', 'superseded')",
            name="document_drafts_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    based_on_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    title_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    body_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class Approval(Base):
    """An approval decision over a draft; one decision is applied once.

    ``payload_ciphertext`` is the canonical ``{"type","title","body"}`` proposal,
    ``payload_sha256`` its integrity hash. ``document_id`` is NULL for a new-proposal
    draft and set to the resulting document when the proposal is confirmed.
    ``idempotency_key`` records the key that produced the terminal ``decision`` so a
    repeated key can return the original result.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'confirmed', 'rejected', 'superseded')",
            name="approvals_status_check",
        ),
        Index("approvals_id_idempotency_key_idx", "id", "idempotency_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class AuditLog(Base):
    """An append-only action trail; no update/delete policy exists on this table."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
