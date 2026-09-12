"""Employee action approval tests: fire/rehire/adjust transitions + idempotency."""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import ApiError
from sqlalchemy import text

TTL = timedelta(minutes=15)


async def _create_employee(service, context, name="李雷", position="运营专员"):
    return await service.create_employee(context, name, position, "负责社区运营")


async def _fire_draft(service, context, employee, key="k1"):
    return await service.create_employee_action_draft(
        context, employee_id=employee.id, action="fire", idempotency_key=key
    )


async def test_fire_approval_sets_inactive(service, user_context):
    employee = await _create_employee(service, user_context)
    draft = await _fire_draft(service, user_context, employee)

    result = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert result.decision == "confirmed"
    assert result.employee is not None
    assert result.employee.status == "inactive"


async def test_rehire_approval_sets_active(service, repository, user_context):
    employee = await _create_employee(service, user_context)
    await repository.set_status(user_context, employee.id, "inactive")

    draft = await service.create_employee_action_draft(
        user_context, employee_id=employee.id, action="rehire", idempotency_key="k1"
    )
    result = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert result.employee is not None and result.employee.status == "active"


async def test_adjust_position_approval_updates_position(service, user_context):
    employee = await _create_employee(service, user_context)
    draft = await service.create_employee_action_draft(
        user_context,
        employee_id=employee.id,
        action="adjust_position",
        position="产品经理",
        idempotency_key="k1",
    )
    result = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert result.employee is not None and result.employee.position == "产品经理"


async def test_adjust_position_requires_position(service, user_context):
    employee = await _create_employee(service, user_context)
    with pytest.raises(ApiError) as exc:
        await service.create_employee_action_draft(
            user_context, employee_id=employee.id, action="adjust_position", idempotency_key="k1"
        )
    assert exc.value.code == "VALIDATION_FAILED"


async def test_reject_keeps_status(service, user_context):
    employee = await _create_employee(service, user_context)
    draft = await _fire_draft(service, user_context, employee)
    result = await service.decide_employee_approval(user_context, draft.approval_id, "reject", "decide-1")
    assert result.decision == "rejected"
    assert result.employee is None

    got = await service.list_employees(user_context)
    assert got[0].status == "active"


async def test_same_key_replay_returns_original(service, repository, user_context):
    employee = await _create_employee(service, user_context)
    draft = await _fire_draft(service, user_context, employee)

    first = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    second = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert second.approval_id == first.approval_id
    assert second.decision == "confirmed"
    assert second.employee is not None and second.employee.status == "inactive"

    # The status flip was applied exactly once.
    rows = await repository.list_all(user_context)
    assert len(rows) == 1 and rows[0].status == "inactive"


async def test_different_key_after_resolution_conflicts(service, user_context):
    employee = await _create_employee(service, user_context)
    draft = await _fire_draft(service, user_context, employee)
    await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")

    with pytest.raises(ApiError) as exc:
        await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-OTHER")
    assert exc.value.code == "APPROVAL_CONFLICT"


async def test_expired_approval_raises(service, user_context, db_session):
    employee = await _create_employee(service, user_context)
    draft = await _fire_draft(service, user_context, employee)
    await db_session.execute(
        text("update employee_approvals set expires_at = :ts where id = :id"),
        {"ts": datetime.now(UTC) - timedelta(seconds=1), "id": str(draft.approval_id)},
    )
    await db_session.commit()

    with pytest.raises(ApiError) as exc:
        await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert exc.value.code == "APPROVAL_EXPIRED"


async def test_cross_user_approval_is_404(service, user_a, user_b):
    employee = await _create_employee(service, user_a)
    draft = await _fire_draft(service, user_a, employee)

    with pytest.raises(ApiError) as exc:
        await service.decide_employee_approval(user_b, draft.approval_id, "approve", "decide-1")
    assert exc.value.code == "NOT_FOUND"


async def test_approve_completes_linked_run(service, chat_repository, user_context):
    session = await chat_repository.create_session(user_context)
    created = await chat_repository.create_run(user_context, session.id, "解雇李雷", "k-run")
    await chat_repository.update_run_status(user_context, created.run_id, "waiting_approval")

    employee = await _create_employee(service, user_context)
    draft = await service.create_employee_action_draft(
        user_context, employee_id=employee.id, action="fire", run_id=created.run_id
    )
    result = await service.decide_employee_approval(user_context, draft.approval_id, "approve", "decide-1")
    assert result.employee is not None and result.employee.status == "inactive"

    run = await chat_repository.get_run(user_context, created.run_id)
    assert run is not None and run.status == "completed"
