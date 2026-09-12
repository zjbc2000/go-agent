"""RLS isolation tests: user B can never see or mutate user A's employees."""

from uuid import uuid4

from tests.employees.conftest import TEST_INTERNAL_TOKEN


async def test_user_b_cannot_read_user_a_employees(service, repository, user_a, user_b):
    await service.create_employee(user_a, "李雷", "运营专员", "运营")

    assert await repository.get(user_b, uuid4()) is None
    assert await repository.list_all(user_b) == []
    assert await repository.list_active(user_b) == []

    # A direct update by user B surfaces as NOT_FOUND (RLS hides the row entirely).
    import pytest
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as exc:
        await service.update_employee(user_b, uuid4(), position="hijack")
    assert exc.value.code == "NOT_FOUND"


async def test_user_b_cannot_decide_user_a_approval(service, user_a, user_b):
    employee = await service.create_employee(user_a, "李雷", "运营专员", "运营")
    draft = await service.create_employee_action_draft(
        user_a, employee_id=employee.id, action="fire", idempotency_key="k1"
    )

    import pytest
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as exc:
        await service.decide_employee_approval(user_b, draft.approval_id, "approve", "k2")
    assert exc.value.code == "NOT_FOUND"


async def test_employee_api_is_user_scoped(client, service, user_a, user_b):
    await service.create_employee(user_a, "李雷", "运营专员", "运营")
    headers_b = {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_b.user_id}",
    }
    listed = client.get("/internal/v1/employees", headers=headers_b)
    assert listed.status_code == 200
    assert listed.json() == []
