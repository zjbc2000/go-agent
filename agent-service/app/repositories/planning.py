"""Encrypted, versioned planning-document repository.

All reads and writes run as the end-user JWT (RLS by ``user_id = auth.uid()``);
there is no unscoped/admin read path for planning content in this MVP. Title and
body are encrypted with the application envelope cipher before any write and
decrypted only after an authorized read. Every versioned write appends a new
``document_versions`` row; a write never mutates an existing version.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import user_scoped_session
from app.models.planning import Approval as ApprovalRecord
from app.models.planning import AuditLog as AuditLogRecord
from app.models.planning import Document as DocumentRecord
from app.models.planning import DocumentDraft as DocumentDraftRecord
from app.models.planning import DocumentVersion as DocumentVersionRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DocumentType = Literal["memory", "interest", "task", "skill"]
DocumentStatus = Literal["active"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Document:
    """A decrypted active planning document with its current version handle."""

    id: uuid.UUID
    user_id: uuid.UUID
    type: DocumentType
    title: str
    body: str
    version: int
    status: DocumentStatus
    current_version_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DocumentVersionInfo:
    """A decrypted, immutable version row of a planning document."""

    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    title: str
    body: str
    created_at: datetime


class DocumentRepository:
    """Persistence boundary for encrypted, versioned planning documents."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    @asynccontextmanager
    async def _transaction(self, context: RequestContext) -> AsyncIterator[AsyncSession]:
        async with user_scoped_session(self._session_factory, context) as session:
            yield session

    @asynccontextmanager
    async def transaction(self, context: RequestContext) -> AsyncIterator[ApprovalTransaction]:
        """Open a user-scoped transaction exposing the approval decision primitives.

        The whole decision is one atomic unit: draft creation, approval locking,
        document activation/versioning, resolution, and audit all commit together.
        """
        async with self._transaction(context) as session:
            yield ApprovalTransaction(self, session, context.user_id, self._cipher)

    def _to_document(self, document: DocumentRecord, current_version_id: uuid.UUID) -> Document:
        return Document(
            id=document.id,
            user_id=document.user_id,
            type=cast(DocumentType, document.type),
            title=self._cipher.decrypt(document.title_ciphertext),
            body=self._cipher.decrypt(document.body_ciphertext),
            version=document.current_version,
            status=cast(DocumentStatus, document.status),
            current_version_id=current_version_id,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def _current_version_row(
        self, session: AsyncSession, document: DocumentRecord
    ) -> DocumentVersionRecord:
        """The version row backing a document's ``current_version`` (RLS-scoped)."""
        row = await session.scalar(
            select(DocumentVersionRecord).where(
                DocumentVersionRecord.document_id == document.id,
                DocumentVersionRecord.version == document.current_version,
            )
        )
        if row is None:
            raise ApiError("INTERNAL_ERROR", "Document has no current version row.", False)
        return row

    async def create_active(
        self, context: RequestContext, type: DocumentType, title: str, body: str
    ) -> Document:
        """Create an active document as version 1. Title/body are ciphertext-only."""
        async with self._transaction(context) as session:
            title_ct = self._cipher.encrypt(title)
            body_ct = self._cipher.encrypt(body)
            version_id = uuid.uuid4()
            document = DocumentRecord(
                id=uuid.uuid4(),
                user_id=context.user_id,
                type=type,
                current_version=1,
                status="active",
                title_ciphertext=title_ct,
                body_ciphertext=body_ct,
            )
            session.add(document)
            session.add(
                DocumentVersionRecord(
                    id=version_id,
                    user_id=context.user_id,
                    document_id=document.id,
                    version=1,
                    title_ciphertext=title_ct,
                    body_ciphertext=body_ct,
                )
            )
            await session.flush()
            return self._to_document(document, version_id)

    async def update_active(
        self, context: RequestContext, document_id: uuid.UUID, title: str, body: str
    ) -> Document:
        """Append a new current version with the new title/body (never mutates a version)."""
        async with self._transaction(context) as session:
            document = await session.scalar(
                select(DocumentRecord).where(DocumentRecord.id == document_id)
            )
            if document is None:
                raise ApiError("NOT_FOUND", "Document not found.", False)
            title_ct = self._cipher.encrypt(title)
            body_ct = self._cipher.encrypt(body)
            next_version = document.current_version + 1
            version_id = uuid.uuid4()
            session.add(
                DocumentVersionRecord(
                    id=version_id,
                    user_id=document.user_id,
                    document_id=document.id,
                    version=next_version,
                    title_ciphertext=title_ct,
                    body_ciphertext=body_ct,
                )
            )
            document.current_version = next_version
            document.title_ciphertext = title_ct
            document.body_ciphertext = body_ct
            document.updated_at = _utcnow()
            await session.flush()
            return self._to_document(document, version_id)

    async def list_active(
        self, context: RequestContext, filter: DocumentType | None = None
    ) -> list[Document]:
        """List the caller's active documents, optionally narrowed to one category."""
        async with self._transaction(context) as session:
            query = select(DocumentRecord).where(DocumentRecord.status == "active")
            if filter is not None:
                query = query.where(DocumentRecord.type == filter)
            rows = (await session.scalars(query.order_by(DocumentRecord.updated_at.desc()))).all()
            documents = []
            for row in rows:
                current = await self._current_version_row(session, row)
                documents.append(self._to_document(row, current.id))
            return documents

    async def get(self, context: RequestContext, document_id: uuid.UUID) -> Document | None:
        """Return the caller's document, or None if the caller does not own it (RLS)."""
        async with self._transaction(context) as session:
            document = await session.scalar(
                select(DocumentRecord).where(DocumentRecord.id == document_id)
            )
            if document is None:
                return None
            current = await self._current_version_row(session, document)
            return self._to_document(document, current.id)

    async def list_versions(
        self, context: RequestContext, document_id: uuid.UUID
    ) -> list[DocumentVersionInfo]:
        """List the caller's version rows for a document, oldest first (RLS).

        The document lookup is user-scoped, so a document the caller does not own is
        never visible and surfaces as NOT_FOUND.
        """
        async with self._transaction(context) as session:
            document = await session.scalar(
                select(DocumentRecord).where(DocumentRecord.id == document_id)
            )
            if document is None:
                raise ApiError("NOT_FOUND", "Document not found.", False)
            rows = (
                await session.scalars(
                    select(DocumentVersionRecord)
                    .where(DocumentVersionRecord.document_id == document_id)
                    .order_by(DocumentVersionRecord.version.asc())
                )
            ).all()
            return [
                DocumentVersionInfo(
                    id=row.id,
                    document_id=row.document_id,
                    version=row.version,
                    title=self._cipher.decrypt(row.title_ciphertext),
                    body=self._cipher.decrypt(row.body_ciphertext),
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def restore_version(
        self, context: RequestContext, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> Document:
        """Restore a prior version's ciphertext as a NEW current version.

        The restored row copies the target version's ciphertext verbatim; no
        plaintext ever round-trips through the service during a restore.
        """
        async with self._transaction(context) as session:
            document = await session.scalar(
                select(DocumentRecord).where(DocumentRecord.id == document_id)
            )
            if document is None:
                raise ApiError("NOT_FOUND", "Document not found.", False)
            target = await session.scalar(
                select(DocumentVersionRecord).where(
                    DocumentVersionRecord.id == version_id,
                    DocumentVersionRecord.document_id == document_id,
                )
            )
            if target is None:
                raise ApiError("NOT_FOUND", "Version not found.", False)
            next_version = document.current_version + 1
            new_version_id = uuid.uuid4()
            session.add(
                DocumentVersionRecord(
                    id=new_version_id,
                    user_id=document.user_id,
                    document_id=document.id,
                    version=next_version,
                    title_ciphertext=target.title_ciphertext,
                    body_ciphertext=target.body_ciphertext,
                )
            )
            document.current_version = next_version
            document.title_ciphertext = target.title_ciphertext
            document.body_ciphertext = target.body_ciphertext
            document.updated_at = _utcnow()
            await session.flush()
            return self._to_document(document, new_version_id)


@dataclass(frozen=True)
class DraftHandle:
    """The durable ids of a newly created proposal draft + approval."""

    approval_id: uuid.UUID
    draft_id: uuid.UUID


@dataclass
class PendingApproval:
    """A locked, unresolved approval plus its owning draft.

    ``resolve`` records the terminal outcome; the row stays the single decision
    record for the approval, so a repeated idempotency_key can replay the original
    result and a different key after resolution surfaces as APPROVAL_CONFLICT.
    """

    approval: ApprovalRecord
    draft: DocumentDraftRecord

    @property
    def payload_ciphertext(self) -> str:
        return self.approval.payload_ciphertext

    def resolve(self, decision: str, resolved_by: uuid.UUID) -> None:
        """Mark the approval terminal (decision is confirmed/rejected/superseded)."""
        self.approval.status = decision
        self.approval.decision = decision
        self.approval.resolved_by = resolved_by
        self.approval.decided_at = _utcnow()
        self.approval.updated_at = _utcnow()


class ApprovalTransaction:
    """A user-scoped approval-decision transaction with the decision primitives.

    Created by ``DocumentRepository.transaction``; every method runs inside the single
    user-scoped transaction opened by that context manager. Reads and writes are
    RLS-filtered to the caller, so an approval not owned by the caller is never
    visible (``lock_pending_approval`` surfaces it as NOT_FOUND).
    """

    def __init__(
        self,
        repository: DocumentRepository,
        session: AsyncSession,
        user_id: uuid.UUID,
        cipher: EnvelopeCipher,
    ) -> None:
        self._repo = repository
        self._session = session
        self._user_id = user_id
        self._cipher = cipher
        self._pending: PendingApproval | None = None

    def _require_pending(self) -> PendingApproval:
        if self._pending is None:
            raise ApiError("INTERNAL_ERROR", "No approval is locked in this transaction.", False)
        return self._pending

    async def create_draft(
        self,
        *,
        type: DocumentType,
        title_ciphertext: str,
        body_ciphertext: str,
        payload_ciphertext: str,
        payload_sha256: str,
        document_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        expires_at: datetime | None,
    ) -> DraftHandle:
        """Insert a pending proposal draft + its approval in one transaction."""
        based_on_version = 0
        if document_id is not None:
            document = await self._session.scalar(
                select(DocumentRecord).where(DocumentRecord.id == document_id)
            )
            if document is None:
                raise ApiError("NOT_FOUND", "Document not found.", False)
            based_on_version = document.current_version
        draft_id = uuid.uuid4()
        approval_id = uuid.uuid4()
        draft = DocumentDraftRecord(
            id=draft_id,
            user_id=self._user_id,
            document_id=document_id,
            based_on_version=based_on_version,
            status="pending",
            title_ciphertext=title_ciphertext,
            body_ciphertext=body_ciphertext,
        )
        approval = ApprovalRecord(
            id=approval_id,
            user_id=self._user_id,
            document_id=document_id,
            draft_id=draft_id,
            status="pending",
            payload_ciphertext=payload_ciphertext,
            payload_sha256=payload_sha256,
            run_id=run_id,
            expires_at=expires_at,
        )
        # Flush the draft first: the ORM models declare no FK metadata, so without an
        # explicit flush the approvals insert can be ordered ahead of its draft row.
        self._session.add(draft)
        await self._session.flush()
        self._session.add(approval)
        await self._session.flush()
        return DraftHandle(approval_id=approval_id, draft_id=draft_id)

    async def lock_pending_approval(self, approval_id: uuid.UUID) -> PendingApproval:
        """Lock the approval row FOR UPDATE and load its draft (RLS-scoped).

        Only the owning user's approval is visible; any other caller sees NOT_FOUND.
        """
        approval = await self._session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.id == approval_id).with_for_update()
        )
        if approval is None:
            raise ApiError("NOT_FOUND", "Approval not found.", False)
        draft = await self._session.scalar(
            select(DocumentDraftRecord).where(DocumentDraftRecord.id == approval.draft_id)
        )
        if draft is None:
            raise ApiError("INTERNAL_ERROR", "Approval has no draft.", False)
        pending = PendingApproval(approval=approval, draft=draft)
        self._pending = pending
        return pending

    async def activate_document(self, final_payload: dict[str, str]) -> DocumentRecord:
        """Return the document an approval activates.

        A new proposal (draft.document_id NULL) creates the document row with a
        placeholder version 0; ``append_version`` writes its real version 1 in the
        same transaction. An edit returns the existing document row.
        """
        pending = self._require_pending()
        if pending.draft.document_id is None:
            # A new proposal's category lives in the canonical payload; the edited
            # payload only carries {title, body}, so the type comes from the original.
            original = self._cipher.decrypt_json(pending.approval.payload_ciphertext)
            document = DocumentRecord(
                id=uuid.uuid4(),
                user_id=pending.approval.user_id,
                type=cast(DocumentType, original["type"]),
                current_version=0,
                status="active",
                title_ciphertext=self._cipher.encrypt(final_payload["title"]),
                body_ciphertext=self._cipher.encrypt(final_payload["body"]),
            )
            self._session.add(document)
            await self._session.flush()
            return document
        existing = await self._session.scalar(
            select(DocumentRecord).where(DocumentRecord.id == pending.draft.document_id)
        )
        if existing is None:
            raise ApiError("NOT_FOUND", "Document not found.", False)
        return existing

    async def append_version(
        self, document: DocumentRecord, final_payload: dict[str, str], user_id: uuid.UUID
    ) -> Document:
        """Append a new version row and bump the document to it (never mutates a version)."""
        title_ct = self._cipher.encrypt(final_payload["title"])
        body_ct = self._cipher.encrypt(final_payload["body"])
        next_version = document.current_version + 1
        version_id = uuid.uuid4()
        self._session.add(
            DocumentVersionRecord(
                id=version_id,
                user_id=user_id,
                document_id=document.id,
                version=next_version,
                title_ciphertext=title_ct,
                body_ciphertext=body_ct,
            )
        )
        document.current_version = next_version
        document.title_ciphertext = title_ct
        document.body_ciphertext = body_ct
        document.updated_at = _utcnow()
        await self._session.flush()
        return self._repo._to_document(document, version_id)

    async def supersede_draft(self, draft_id: uuid.UUID) -> None:
        """Mark a draft superseded so it can never be confirmed."""
        draft = await self._session.scalar(
            select(DocumentDraftRecord).where(DocumentDraftRecord.id == draft_id)
        )
        if draft is None:
            raise ApiError("INTERNAL_ERROR", "Draft not found.", False)
        draft.status = "superseded"
        draft.superseded_at = _utcnow()
        draft.updated_at = _utcnow()
        await self._session.flush()

    async def audit(
        self,
        action: str,
        document_id: uuid.UUID | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit row for the locked approval's user (insert-only trail)."""
        pending = self._require_pending()
        detail_ct = self._cipher.encrypt(json.dumps(detail)) if detail is not None else None
        self._session.add(
            AuditLogRecord(
                id=uuid.uuid4(),
                user_id=pending.approval.user_id,
                document_id=document_id,
                action=action,
                detail_ciphertext=detail_ct,
            )
        )
        await self._session.flush()

    async def load_document(self, document_id: uuid.UUID) -> Document | None:
        """Return the caller's document DTO, or None if not visible (RLS)."""
        row = await self._session.scalar(
            select(DocumentRecord).where(DocumentRecord.id == document_id)
        )
        if row is None:
            return None
        current = await self._repo._current_version_row(self._session, row)
        return self._repo._to_document(row, current.id)
