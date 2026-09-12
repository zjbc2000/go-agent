"""Employee repository tests: encryption at rest, list/get/update/set_status."""

from uuid import uuid4

from sqlalchemy import text


async def test_create_encrypts_at_rest_and_decrypts_on_read(service, repository, user_context, db_session):
    employee = await service.create_employee(user_context, "李雷", "运营专员", "负责社区运营与活动策划")
    row = await db_session.execute(
        text("select name_ciphertext, position_ciphertext, prompt_ciphertext from employees where id = :id"),
        {"id": str(employee.id)},
    )
    (name_ct, position_ct, prompt_ct) = row.one()
    assert "李雷" not in name_ct
    assert "运营专员" not in position_ct
    assert "负责社区运营" not in prompt_ct

    got = await repository.get(user_context, employee.id)
    assert got is not None
    assert got.name == "李雷"
    assert got.position == "运营专员"
    assert got.prompt == "负责社区运营与活动策划"
    assert got.status == "active"


async def test_list_returns_all_list_active_only_active(repository, service, user_context):
    await service.create_employee(user_context, "李雷", "运营专员", "运营")
    fired = await service.create_employee(user_context, "韩梅梅", "设计师", "设计")
    await repository.set_status(user_context, fired.id, "inactive")

    all_rows = await repository.list_all(user_context)
    assert len(all_rows) == 2

    active = await repository.list_active(user_context)
    assert [e.name for e in active] == ["李雷"]


async def test_get_missing_returns_none(repository, user_context):
    assert await repository.get(user_context, uuid4()) is None


async def test_update_employee_fields(service, repository, user_context):
    employee = await service.create_employee(user_context, "李雷", "运营专员", "运营")
    updated = await service.update_employee(user_context, employee.id, position="产品经理", prompt="负责产品规划")
    assert updated.position == "产品经理"
    assert updated.prompt == "负责产品规划"
    assert updated.name == "李雷"

    got = await repository.get(user_context, employee.id)
    assert got is not None and got.position == "产品经理"


async def test_update_missing_returns_not_found(service, user_context):
    import pytest
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as exc:
        await service.update_employee(user_context, uuid4(), name="x")
    assert exc.value.code == "NOT_FOUND"


async def test_set_status_flips_lifecycle(repository, service, user_context):
    employee = await service.create_employee(user_context, "李雷", "运营专员", "运营")
    assert employee.status == "active"

    fired = await repository.set_status(user_context, employee.id, "inactive")
    assert fired is not None and fired.status == "inactive"

    rehired = await repository.set_status(user_context, employee.id, "active")
    assert rehired is not None and rehired.status == "active"
