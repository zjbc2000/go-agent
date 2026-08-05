"""RLS isolation tests: a user never observes or mutates another user's documents.

Every repository query runs as the ``authenticated`` role under the caller's
JWT claims, so PostgreSQL row-level security filters rows by ``user_id``.
"""

import pytest
from app.core.errors import ApiError


async def test_user_cannot_read_another_users_document(repository, user_a, user_b):
    document = await repository.create_active(user_a, type="memory", title="private", body="secret")
    assert await repository.get(user_b, document.id) is None


async def test_user_b_receives_no_documents_for_user_a(repository, user_a, user_b):
    await repository.create_active(user_a, type="memory", title="private", body="secret")
    assert await repository.list_active(user_b, None) == []


async def test_user_b_cannot_update_another_users_document(repository, user_a, user_b):
    document = await repository.create_active(user_a, type="memory", title="private", body="secret")
    with pytest.raises(ApiError) as excinfo:
        await repository.update_active(user_b, document.id, title="hijacked", body="x")
    assert excinfo.value.code == "NOT_FOUND"


async def test_user_b_cannot_restore_another_users_document(repository, user_a, user_b):
    document = await repository.create_active(user_a, type="memory", title="private", body="secret")
    with pytest.raises(ApiError) as excinfo:
        await repository.restore_version(user_b, document.id, document.current_version_id)
    assert excinfo.value.code == "NOT_FOUND"
