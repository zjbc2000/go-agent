"""Execution approval tests: write steps need an expiring approval, read-only
steps queue a run directly, idempotency returns the same result, and RLS keeps a
skill owner-isolated. A skill is a planning document of category ``skill`` whose
body is the JSON manifest; the service loads it user-scoped.
"""

import json
from datetime import timedelta

import pytest
from app.core.errors import ApiError
from sqlalchemy import text


async def test_write_step_creates_expiring_approval(service, user_context, active_skill):
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "run-1")
    assert result.approval is not None
    assert result.approval.expires_at - result.approval.created_at == timedelta(minutes=15)


async def test_read_only_plan_queues_run_directly(service, repository, user_context, active_read_skill):
    target = await repository.create_active(user_context, type="task", title="target", body="body")
    result = await service.request_execution(
        user_context, active_read_skill, {"document_id": str(target.id)}, "run-read-1"
    )
    assert result.run is not None
    assert result.run.status == "queued"
    assert result.approval is None


async def test_repeat_idempotency_key_returns_same_approval(service, user_context, active_skill, db_session):
    first = await service.request_execution(user_context, active_skill, {"title": "x"}, "same-key")
    second = await service.request_execution(user_context, active_skill, {"title": "x"}, "same-key")
    assert first.approval is not None and second.approval is not None
    assert second.approval.id == first.approval.id
    approvals = await db_session.scalar(text("select count(*) from execution_approvals"))
    assert approvals == 1


async def test_repeat_idempotency_key_returns_same_run(
    service, repository, user_context, active_read_skill, db_session
):
    target = await repository.create_active(user_context, type="task", title="target", body="body")
    inputs = {"document_id": str(target.id)}
    first = await service.request_execution(user_context, active_read_skill, inputs, "run-same")
    second = await service.request_execution(user_context, active_read_skill, inputs, "run-same")
    assert first.run is not None and second.run is not None
    assert second.run.id == first.run.id
    runs = await db_session.scalar(text("select count(*) from sandbox_runs"))
    assert runs == 1


async def test_non_skill_document_is_invalid(service, repository, user_context):
    document = await repository.create_active(user_context, type="task", title="not a skill", body="plain")
    with pytest.raises(ApiError) as excinfo:
        await service.request_execution(user_context, document.id, {"title": "x"}, "key-1")
    assert excinfo.value.code == "SKILL_INVALID"


async def test_skill_with_no_steps_is_invalid(service, repository, user_context):
    manifest = {"schema_version": 1, "steps": []}
    document = await repository.create_active(user_context, type="skill", title="empty", body=json.dumps(manifest))
    with pytest.raises(ApiError) as excinfo:
        await service.request_execution(user_context, document.id, {}, "key-2")
    assert excinfo.value.code == "SKILL_INVALID"


async def test_user_b_cannot_execute_user_a_skill(service, repository, user_a, user_b):
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.create", "input": {"type": "task", "title": "x", "body": "b"}}],
    }
    skill = await repository.create_active(user_a, type="skill", title="a-skill", body=json.dumps(manifest))
    with pytest.raises(ApiError) as excinfo:
        await service.request_execution(user_b, skill.id, {"title": "x"}, "key-3")
    assert excinfo.value.code == "NOT_FOUND"


async def test_approval_and_run_store_ciphertext_only(
    service, user_context, active_skill, active_read_skill, db_session
):
    await service.request_execution(user_context, active_skill, {"title": "sensitive-title"}, "cipher-1")
    await service.request_execution(
        user_context, active_read_skill, {"document_id": "00000000-0000-0000-0000-000000000010"}, "cipher-2"
    )
    approval = (await db_session.execute(text("select inputs_ciphertext from execution_approvals"))).one()
    assert "sensitive-title" not in approval[0]
    run = (await db_session.execute(text("select inputs_ciphertext from sandbox_runs"))).one()
    assert run[0] and "sensitive-title" not in run[0]
