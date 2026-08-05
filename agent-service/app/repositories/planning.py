"""Encrypted, versioned planning-document repository.

All reads and writes run as the end-user JWT (RLS by ``user_id = auth.uid()``);
there is no unscoped/admin read path for planning content in this MVP. Title and
body are encrypted with the application envelope cipher before any write and
decrypted only after an authorized read. Every versioned write appends a new
``document_versions`` row; a write never mutates an existing version.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.db.session import user_scoped_session
from app.models.planning import Document as DocumentRecord
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


class DocumentRepository:
    """Persistence boundary for encrypted, versioned planning documents."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    @asynccontextmanager
    async def _transaction(self, context: RequestContext) -> AsyncIterator[AsyncSession]:
        async with user_scoped_session(self._session_factory, context) as session:
            yield session

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
