"""DocumentRepository tests: versioned writes, encryption at rest, listing, restore."""

from sqlalchemy import text


async def test_create_active_returns_version_one(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    assert document.type == "task"
    assert document.title == "v1"
    assert document.body == "first"
    assert document.version == 1
    assert document.status == "active"


async def test_update_active_appends_a_new_version(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    updated = await repository.update_active(user_context, document.id, title="v2", body="second")
    assert updated.version == document.version + 1
    assert updated.title == "v2"
    assert updated.body == "second"
    assert updated.current_version_id != document.current_version_id


async def test_restoring_a_version_switches_current_version(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    updated = await repository.update_active(user_context, document.id, title="v2", body="second")
    # restore the CURRENT version (v2) -> switches current_version to it (stays v2,
    # no new version row created).
    restored = await repository.restore_version(user_context, document.id, updated.current_version_id)
    assert restored.version == updated.version


async def test_restore_returns_target_version_content(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    await repository.update_active(user_context, document.id, title="v2", body="second")
    restored = await repository.restore_version(user_context, document.id, document.current_version_id)
    assert restored.title == "v1"
    assert restored.body == "first"


async def test_every_write_appends_a_version_row(db_session, repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    await repository.update_active(user_context, document.id, title="v2", body="second")
    # Restore switches current_version (no new row) — version count stays 2.
    await repository.restore_version(user_context, document.id, document.current_version_id)
    count = await db_session.scalar(text("select count(*) from document_versions"))
    assert count == 2


async def test_raw_columns_never_contain_plaintext(db_session, repository, user_context):
    await repository.create_active(user_context, type="task", title="private title", body="secret body")
    document = (await db_session.execute(text("select title_ciphertext, body_ciphertext from documents"))).one()
    assert "private title" not in document[0]
    assert "secret body" not in document[1]
    version = (await db_session.execute(text("select title_ciphertext, body_ciphertext from document_versions"))).one()
    assert "private title" not in version[0]
    assert "secret body" not in version[1]


async def test_get_returns_decrypted_document(repository, user_context):
    document = await repository.create_active(user_context, type="memory", title="m", body="b")
    fetched = await repository.get(user_context, document.id)
    assert fetched is not None
    assert fetched.title == "m"
    assert fetched.body == "b"
    assert fetched.version == 1
    assert fetched.current_version_id == document.current_version_id


async def test_list_active_filters_by_type(repository, user_context):
    await repository.create_active(user_context, type="task", title="t", body="b")
    await repository.create_active(user_context, type="memory", title="m", body="b")
    tasks = await repository.list_active(user_context, "task")
    assert [d.type for d in tasks] == ["task"]
    all_docs = await repository.list_active(user_context, None)
    assert len(all_docs) == 2


async def test_list_active_returns_latest_content(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    updated = await repository.update_active(user_context, document.id, title="v2", body="second")
    listed = await repository.list_active(user_context, "task")
    assert len(listed) == 1
    assert listed[0].title == "v2"
    assert listed[0].version == updated.version
