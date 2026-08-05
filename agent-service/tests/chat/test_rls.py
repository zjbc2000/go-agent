"""RLS isolation tests: a user never observes another user's chat data.

Every repository query runs as the ``authenticated`` role under the caller's
JWT claims, so PostgreSQL row-level security filters rows by ``user_id``.
"""

import uuid

SESSION_ID = uuid.uuid4()


async def test_user_b_receives_no_rows_for_user_a_session(chat_repository, user_a_context, user_b_context):
    await chat_repository.create_run(user_a_context, SESSION_ID, "secret for A", "req-a")
    messages = await chat_repository.list_messages(user_b_context, SESSION_ID)
    assert messages == []


async def test_idempotency_key_is_scoped_to_the_user(chat_repository, user_a_context, user_b_context):
    run_a = await chat_repository.create_run(user_a_context, SESSION_ID, "hello", "shared-key")
    run_b = await chat_repository.create_run(user_b_context, SESSION_ID, "hello", "shared-key")
    assert run_a.run_id != run_b.run_id
