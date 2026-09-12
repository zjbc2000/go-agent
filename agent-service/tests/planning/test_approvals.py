"""Approval workflow tests: draft creation, immutable decisions, idempotency, audit.

Decisions run as the caller's JWT under RLS; an approval is lockable only by its
owner. A confirmed approval activates a document (creating it for a new proposal or
appending a version for an edit), a rejected approval preserves the draft and writes
no document, and a regenerate supersedes the draft. A repeated idempotency_key
returns the original result without a duplicate version.
"""

import uuid

import pytest
from app.core.errors import ApiError
from sqlalchemy import text


async def test_edit_confirm_preserves_original_and_writes_edited_version(service, user_context):
    draft = await service.create_document_draft(user_context, type="interest", title="original", body="model")
    result = await service.decide_document_approval(
        user_context, draft.approval_id, "approve", {"title": "edited", "body": "user"}, "decision-1"
    )
    assert result.document is not None
    assert result.document.title == "edited"
    assert result.document.body == "user"
    assert result.original_payload["title"] == "original"
    assert result.original_payload["body"] == "model"


async def test_second_decision_returns_conflict(service, user_context, pending_approval):
    await service.decide_document_approval(user_context, pending_approval, "reject", None, "decision-1")
    with pytest.raises(ApiError, match="APPROVAL_CONFLICT"):
        await service.decide_document_approval(user_context, pending_approval, "approve", None, "decision-2")


async def test_approve_without_edit_creates_document_from_original_payload(service, user_context, db_session):
    draft = await service.create_document_draft(user_context, type="task", title="original", body="model")
    result = await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "key-a")
    assert result.document is not None
    assert result.document.version == 1
    assert result.document.title == "original"
    assert result.document.body == "model"
    assert result.version == 1
    assert result.decision == "confirmed"
    assert result.original_payload == {"type": "task", "title": "original", "body": "model"}
    # Exactly one document and one version row were written.
    documents = await db_session.scalar(text("select count(*) from documents"))
    versions = await db_session.scalar(text("select count(*) from document_versions"))
    assert documents == 1
    assert versions == 1


async def test_approve_is_idempotent_with_same_key(service, user_context, db_session):
    draft = await service.create_document_draft(user_context, type="task", title="original", body="model")
    first = await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "same-key")
    second = await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "same-key")
    assert second.document is not None
    assert second.document.id == first.document.id
    assert second.document.version == first.document.version
    assert second.original_payload == first.original_payload
    versions = await db_session.scalar(text("select count(*) from document_versions"))
    assert versions == 1


async def test_edit_confirm_on_existing_document_appends_version(service, repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    draft = await service.create_document_draft(
        user_context, type="task", title="v2", body="second", document_id=document.id
    )
    result = await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "key-b")
    assert result.document is not None
    assert result.document.version == 2
    assert result.document.title == "v2"
    assert result.document.body == "second"
    fetched = await repository.get(user_context, document.id)
    assert fetched is not None
    assert fetched.version == 2
    assert fetched.title == "v2"


async def test_reject_preserves_draft_and_writes_no_document(service, user_context, db_session):
    draft = await service.create_document_draft(user_context, type="task", title="proposal", body="body")
    result = await service.decide_document_approval(user_context, draft.approval_id, "reject", None, "key-c")
    assert result.document is None
    assert result.version is None
    assert result.decision == "rejected"
    documents = await db_session.scalar(text("select count(*) from documents"))
    assert documents == 0
    row = (
        await db_session.execute(text("select status from document_drafts where id = :id"), {"id": draft.draft_id})
    ).one()
    assert row[0] == "pending"
    action = await db_session.scalar(text("select action from audit_logs"))
    assert action == "document.rejected"


async def test_regenerate_supersedes_draft_and_writes_no_document(service, user_context, db_session):
    draft = await service.create_document_draft(user_context, type="task", title="proposal", body="body")
    result = await service.decide_document_approval(user_context, draft.approval_id, "regenerate", None, "key-d")
    assert result.document is None
    assert result.decision == "superseded"
    row = (
        await db_session.execute(
            text("select status, superseded_at from document_drafts where id = :id"), {"id": draft.draft_id}
        )
    ).one()
    assert row[0] == "superseded"
    assert row[1] is not None
    documents = await db_session.scalar(text("select count(*) from documents"))
    assert documents == 0
    action = await db_session.scalar(text("select action from audit_logs"))
    assert action == "document.regenerate"


async def test_expired_approval_raises_approval_expired(service, user_context, db_session):
    draft = await service.create_document_draft(user_context, type="task", title="proposal", body="body")
    await db_session.execute(
        text("update approvals set expires_at = now() - interval '1 day' where id = :id"),
        {"id": draft.approval_id},
    )
    await db_session.commit()
    with pytest.raises(ApiError) as excinfo:
        await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "key-e")
    assert excinfo.value.code == "APPROVAL_EXPIRED"


async def test_user_b_cannot_decide_user_as_approval(service, user_a, user_b):
    draft = await service.create_document_draft(user_a, type="task", title="private", body="secret")
    with pytest.raises(ApiError) as excinfo:
        await service.decide_document_approval(user_b, draft.approval_id, "approve", None, "key-f")
    assert excinfo.value.code == "NOT_FOUND"


async def test_draft_creation_sets_payload_sha256_and_encrypts_draft(service, user_context, db_session, cipher):
    draft = await service.create_document_draft(
        user_context, type="memory", title="sensitive title", body="sensitive body"
    )
    approval = (
        await db_session.execute(
            text("select payload_ciphertext, payload_sha256 from approvals where id = :id"),
            {"id": draft.approval_id},
        )
    ).one()
    # The canonical payload is decryptable and hashes to the stored SHA-256.
    decrypted = cipher.decrypt(approval[0])
    assert "sensitive title" in decrypted
    assert approval[1] is not None and len(approval[1]) == 64
    # Raw draft columns never hold plaintext.
    row = (
        await db_session.execute(
            text("select title_ciphertext, body_ciphertext from document_drafts where id = :id"),
            {"id": draft.draft_id},
        )
    ).one()
    assert "sensitive title" not in row[0]
    assert "sensitive body" not in row[1]


async def test_approve_emits_run_completed_for_linked_run(service, chat_repository, user_context, db_session):
    created = await chat_repository.create_run(user_context, uuid.uuid4(), "draft prompt", "run-key-1")
    draft = await service.create_document_draft(
        user_context, type="task", title="proposal", body="confirmed body", run_id=created.run_id
    )
    result = await service.decide_document_approval(user_context, draft.approval_id, "approve", None, "key-g")
    assert result.document is not None
    assert result.document.body == "confirmed body"

    run = await chat_repository.get_run(user_context, created.run_id)
    assert run is not None
    assert run.status == "completed"
    messages = await chat_repository.list_messages(user_context, created.session_id)
    assistant = [m for m in messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "confirmed body"
    events = await chat_repository.list_events(user_context, created.run_id, after=0)
    assert any(e.kind == "run.completed" for e in events)
