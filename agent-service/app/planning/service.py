"""Planning service: proposal drafts and immutable approval decisions.

A draft stores a canonical encrypted payload (``{"type","title","body"}``) plus its
SHA-256. ``decide_document_approval`` applies exactly one terminal decision per
approval: approve activates a document (creating it for a new proposal or appending
a version for an edit), reject preserves the draft, and regenerate supersedes it.
Idempotent: a repeated ``idempotency_key`` returns the original result with no
duplicate version. The approval transaction is the atomic unit; the ``run.completed``
follow-on for a linked run is emitted only after it commits.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from app.chat.service import ChatService
from app.core.context import RequestContext
from app.core.crypto import EnvelopeCipher
from app.core.errors import ApiError
from app.planning.schemas import ApprovalResult, DraftApproval
from app.repositories.planning import ApprovalTransaction, DocumentRepository, DocumentType, PendingApproval

_APPROVAL_TTL = timedelta(days=7)

_VALID_TYPES: tuple[DocumentType, ...] = ("memory", "interest", "task", "skill")
_VALID_DECISIONS: tuple[str, ...] = ("approve", "reject", "regenerate")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlanningService:
    """Creates proposal drafts and applies immutable approval decisions."""

    def __init__(
        self,
        repository: DocumentRepository,
        cipher: EnvelopeCipher,
        chat: ChatService | None = None,
        approval_ttl: timedelta = _APPROVAL_TTL,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._chat = chat
        self._approval_ttl = approval_ttl

    async def create_document_draft(
        self,
        context: RequestContext,
        type: str,
        title: str,
        body: str,
        *,
        document_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> DraftApproval:
        """Create a pending proposal draft + approval with a canonical encrypted payload.

        Idempotent per ``run_id``: a pending draft already linked to the run (e.g. a
        reconnect re-running the assistant graph) returns the existing approval.
        """
        if type not in _VALID_TYPES:
            raise ApiError(
                "VALIDATION_FAILED", "type must be one of memory, interest, task, skill.", False
            )
        if not isinstance(title, str) or not title.strip():
            raise ApiError("VALIDATION_FAILED", "title is required.", False)
        if not isinstance(body, str) or not body.strip():
            raise ApiError("VALIDATION_FAILED", "body is required.", False)
        if run_id is not None:
            existing = await self._repository.get_pending_draft_for_run(context, run_id)
            if existing is not None:
                return existing

        typed: DocumentType = cast(DocumentType, type)
        payload = {"type": typed, "title": title, "body": body}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_ciphertext = self._cipher.encrypt(canonical)
        payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        async with self._repository.transaction(context) as tx:
            handle = await tx.create_draft(
                type=typed,
                title_ciphertext=self._cipher.encrypt(title),
                body_ciphertext=self._cipher.encrypt(body),
                payload_ciphertext=payload_ciphertext,
                payload_sha256=payload_sha256,
                document_id=document_id,
                run_id=run_id,
                expires_at=_utcnow() + self._approval_ttl,
            )
            return DraftApproval(
                approval_id=handle.approval_id,
                draft_id=handle.draft_id,
                document_id=document_id,
                type=typed,
                title=title,
                body=body,
            )

    async def decide_document_approval(
        self,
        context: RequestContext,
        approval_id: UUID,
        decision: str,
        edited_payload: dict[str, str] | None,
        idempotency_key: str,
    ) -> ApprovalResult:
        """Apply an immutable approval decision with idempotency and conflict detection."""
        if decision not in _VALID_DECISIONS:
            raise ApiError(
                "VALIDATION_FAILED", "decision must be one of approve, reject, regenerate.", False
            )
        if edited_payload is not None and not (
            isinstance(edited_payload.get("title"), str) and isinstance(edited_payload.get("body"), str)
        ):
            raise ApiError(
                "VALIDATION_FAILED", "edited_payload must have title and body strings.", False
            )

        run_id: UUID | None = None
        async with self._repository.transaction(context) as tx:
            pending = await tx.lock_pending_approval(approval_id)
            run_id = pending.approval.run_id
            if pending.approval.status != "pending":
                if pending.approval.idempotency_key == idempotency_key:
                    # Idempotent repeat: return the ORIGINAL result, never re-decide.
                    return await self._replay_resolved(tx, pending)
                raise ApiError(
                    "APPROVAL_CONFLICT",
                    "Approval was already decided; use the original idempotency_key.",
                    False,
                )
            if pending.approval.expires_at is not None and pending.approval.expires_at <= _utcnow():
                raise ApiError("APPROVAL_EXPIRED", "Approval has expired.", False)
            result = await self._decide(tx, context, pending, decision, edited_payload, idempotency_key)

        # Follow-on notification AFTER the approval transaction commits. The approval
        # transaction is the atomic unit; the run event is a follow-on. Any terminal
        # decision (approve/reject/regenerate) completes the linked run; approve passes
        # the activated document body, the others pass None (ChatService keeps the
        # already-streamed explanation as the assistant message content).
        if run_id is not None and self._chat is not None:
            content = result.document.body if result.document is not None else None
            await self._chat.complete_run_with_message(context, run_id, content)
        return result

    async def _decide(
        self,
        tx: ApprovalTransaction,
        context: RequestContext,
        pending: PendingApproval,
        decision: str,
        edited_payload: dict[str, str] | None,
        idempotency_key: str,
    ) -> ApprovalResult:
        original_payload = self._cipher.decrypt_json(pending.approval.payload_ciphertext)
        if decision == "approve":
            final_payload = edited_payload or original_payload
            document_row = await tx.activate_document(final_payload)
            document = await tx.append_version(document_row, final_payload, context.user_id)
            pending.approval.document_id = document.id
            pending.approval.idempotency_key = idempotency_key
            pending.resolve("confirmed", context.user_id)
            await tx.audit("document.approved", document.id, {"version": document.version})
            return ApprovalResult(
                document=document,
                original_payload=original_payload,
                version=document.version,
                approval_id=pending.approval.id,
                decision="confirmed",
            )
        if decision == "reject":
            pending.approval.idempotency_key = idempotency_key
            pending.resolve("rejected", context.user_id)
            await tx.audit("document.rejected", pending.draft.document_id)
            return ApprovalResult(
                document=None,
                original_payload=original_payload,
                version=None,
                approval_id=pending.approval.id,
                decision="rejected",
            )
        # regenerate: the proposal is sent back; its draft can never be confirmed.
        pending.approval.idempotency_key = idempotency_key
        pending.resolve("superseded", context.user_id)
        await tx.supersede_draft(pending.draft.id)
        await tx.audit("document.regenerate", pending.draft.document_id)
        return ApprovalResult(
            document=None,
            original_payload=original_payload,
            version=None,
            approval_id=pending.approval.id,
            decision="superseded",
        )

    async def _replay_resolved(
        self, tx: ApprovalTransaction, pending: PendingApproval
    ) -> ApprovalResult:
        """Rebuild the original result for an idempotent repeated key."""
        original_payload = self._cipher.decrypt_json(pending.approval.payload_ciphertext)
        decision = pending.approval.decision or pending.approval.status
        document = None
        version = None
        if decision == "confirmed" and pending.approval.document_id is not None:
            document = await tx.load_document(pending.approval.document_id)
            if document is not None:
                version = document.version
        return ApprovalResult(
            document=document,
            original_payload=original_payload,
            version=version,
            approval_id=pending.approval.id,
            decision=decision,
        )
