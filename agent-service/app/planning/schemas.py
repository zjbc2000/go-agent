"""Planning service DTOs: a proposal draft and its immutable approval result.

These are plain immutable dataclasses (matching the chat/planning repository DTO
convention); the router serializes them into the API envelope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.repositories.planning import Document, DocumentType


@dataclass(frozen=True)
class DraftApproval:
    """A created proposal draft with its pending approval handle."""

    approval_id: uuid.UUID
    draft_id: uuid.UUID
    document_id: uuid.UUID | None
    type: DocumentType
    title: str
    body: str


@dataclass(frozen=True)
class ApprovalResult:
    """The immutable outcome of an approval decision.

    ``document`` is the activated document on approve and None on reject/regenerate;
    ``original_payload`` is the decrypted canonical proposal so a client can diff the
    edit; ``version`` is the resulting document version on approve, else None.
    """

    document: Document | None
    original_payload: dict[str, str]
    version: int | None
    approval_id: uuid.UUID
    decision: str
