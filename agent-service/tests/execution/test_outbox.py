"""Transactional outbox tests: ``confirm_execution`` and the read-only queue
path both insert the run and its ``sandbox.execute`` outbox row in ONE
transaction, a repeated key is idempotent (no second event), a different key
after resolution conflicts, an expired approval is rejected, a cross-user confirm
is NOT_FOUND, and the tightened ``UNIQUE(document_id, idempotency_key)``
constraint rejects a concurrent duplicate.
"""

import json
import uuid

import pytest
from app.core.errors import ApiError
from app.execution.outbox import SANDBOX_EXECUTE_TASK
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

_WRITE_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {
            "id": "s1",
            "tool": "document.create",
            "input": {"type": "task", "title": "{{title}}", "body": "skill body"},
        }
    ],
}


async def test_confirming_execution_creates_unpublished_outbox_row(service, user_context, approval, outbox):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    assert await outbox.has_event(SANDBOX_EXECUTE_TASK, run.id)
    assert await outbox.event_status(SANDBOX_EXECUTE_TASK, run.id) == "pending"


async def test_read_only_run_creates_outbox_event_in_same_transaction(
    service, repository, user_context, active_read_skill, outbox
):
    target = await repository.create_active(user_context, type="task", title="target", body="body")
    result = await service.request_execution(
        user_context, active_read_skill, {"document_id": str(target.id)}, "read-key-1"
    )
    assert result.run is not None
    assert await outbox.has_event(SANDBOX_EXECUTE_TASK, result.run.id)


async def test_repeated_confirm_key_returns_same_run_without_second_event(
    service, user_context, approval, outbox, db_session
):
    first = await service.confirm_execution(user_context, approval.id, "confirm-same")
    second = await service.confirm_execution(user_context, approval.id, "confirm-same")
    assert second.id == first.id
    events = await db_session.scalar(text("select count(*) from outbox_events where event_type = 'sandbox.execute'"))
    assert events == 1


async def test_confirm_with_different_key_after_resolution_conflicts(service, user_context, approval):
    await service.confirm_execution(user_context, approval.id, "confirm-1")
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_execution(user_context, approval.id, "confirm-2")
    assert excinfo.value.code == "APPROVAL_CONFLICT"


async def test_confirm_expired_approval_is_rejected(service, user_context, approval, db_session):
    await db_session.execute(
        text("update execution_approvals set expires_at = now() - interval '1 minute' where id = :id"),
        {"id": approval.id},
    )
    await db_session.commit()
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_execution(user_context, approval.id, "confirm-expired")
    assert excinfo.value.code == "APPROVAL_EXPIRED"
    assert excinfo.value.retryable is False


async def test_confirm_another_users_approval_is_not_found(service, repository, user_a, user_b):
    skill = await repository.create_active(user_a, type="skill", title="a-skill", body=json.dumps(_WRITE_MANIFEST))
    result = await service.request_execution(user_a, skill.id, {"title": "x"}, "req-key")
    assert result.approval is not None
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_execution(user_b, result.approval.id, "confirm-x")
    assert excinfo.value.code == "NOT_FOUND"


async def test_outbox_payload_contains_only_sandbox_run_id(service, user_context, approval, db_session):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    payload = await db_session.scalar(
        text("select payload from outbox_events where aggregate_id = :rid"), {"rid": run.id}
    )
    assert payload == {"sandbox_run_id": str(run.id)}


async def test_confirm_run_resolves_approval_and_queues_run(service, user_context, approval, db_session):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    assert run.status == "queued"
    approval_status = await db_session.scalar(
        text("select status from execution_approvals where id = :id"), {"id": approval.id}
    )
    assert approval_status == "confirmed"


async def test_document_id_idempotency_unique_rejects_duplicate(
    service, repository, user_context, active_read_skill, engine
):
    target = await repository.create_active(user_context, type="task", title="target", body="body")
    result = await service.request_execution(
        user_context, active_read_skill, {"document_id": str(target.id)}, "dup-key"
    )
    assert result.run is not None
    # A second row with the same (document_id, idempotency_key) must be rejected by
    # the database, not silently allowed (the Task-1 review's tightened constraint).
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    insert into sandbox_runs
                      (id, user_id, document_id, version_id, plan_hash, status, inputs_ciphertext, idempotency_key)
                    values
                      (:id, :uid, :doc, :ver, :hash, 'queued', :ct, 'dup-key')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_context.user_id,
                    "doc": active_read_skill,
                    "ver": uuid.uuid4(),
                    "hash": "x" * 64,
                    "ct": "ciphertext",
                },
            )
            await session.commit()
