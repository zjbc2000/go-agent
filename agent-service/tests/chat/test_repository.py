"""ChatRepository tests: idempotent run creation, encryption at rest, event append, listing."""

import uuid

from app.repositories.chat import CreatedRun, Message, StreamEvent
from sqlalchemy import text

SESSION_ID = uuid.uuid4()


async def test_create_run_is_idempotent(chat_repository, user_context):
    first = await chat_repository.create_run(user_context, SESSION_ID, "hello", "req-1")
    second = await chat_repository.create_run(user_context, SESSION_ID, "hello", "req-1")
    assert second.run_id == first.run_id


async def test_raw_message_column_never_contains_plaintext(db_session, chat_repository, user_context):
    await chat_repository.create_run(user_context, SESSION_ID, "private", "req-2")
    rows = (await db_session.execute(text("select content_ciphertext from messages"))).all()
    assert len(rows) == 2  # user message + assistant placeholder
    assert all("private" not in row[0] for row in rows)


async def test_create_run_returns_queued_run(chat_repository, user_context):
    run = await chat_repository.create_run(user_context, SESSION_ID, "hi", "req-3")
    assert isinstance(run, CreatedRun)
    assert run.session_id == SESSION_ID
    assert run.status == "queued"


async def test_list_messages_returns_user_then_assistant(chat_repository, user_context):
    await chat_repository.create_run(user_context, SESSION_ID, "hello there", "req-4")
    messages = await chat_repository.list_messages(user_context, SESSION_ID)
    assert isinstance(messages[0], Message)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hello there"


async def test_append_event_increments_sequence_per_run(chat_repository, user_context):
    run = await chat_repository.create_run(user_context, SESSION_ID, "hi", "req-5")
    first = await chat_repository.append_event(run.run_id, "run.started", '{"ok": true}')
    second = await chat_repository.append_event(run.run_id, "message.delta", "part1")
    assert isinstance(first, StreamEvent)
    assert first.sequence == 1
    assert second.sequence == 2


async def test_stream_event_payload_never_contains_plaintext(db_session, chat_repository, user_context):
    run = await chat_repository.create_run(user_context, SESSION_ID, "hi", "req-6")
    await chat_repository.append_event(run.run_id, "message.delta", "sensitive-delta")
    raw = await db_session.scalar(text("select payload_ciphertext from stream_events limit 1"))
    assert "sensitive-delta" not in raw


async def test_create_employee_session_binds_employee(chat_repository, cipher, user_context, db_session):
    # employees.user_id references auth.users(id); the chat conftest's synthetic
    # user needs a matching row before any employee insert.
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_context.user_id},
    )
    await db_session.commit()

    from app.repositories.employees import EmployeeRepository

    employee_repo = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repo.create(user_context, "苏曼", "秘书", "负责安排日程与会议纪要")
    session = await chat_repository.create_session(user_context, "[秘书]苏曼", employee.id)
    assert session.employee_id == employee.id

    bound = await chat_repository.get_session_employee_id(user_context, session.id)
    assert bound == employee.id

    # A normal session has no employee binding.
    normal = await chat_repository.create_session(user_context, "普通对话")
    assert normal.employee_id is None
    assert await chat_repository.get_session_employee_id(user_context, normal.id) is None
