"""API tests for the internal planning document routes.

Covers ``GET /internal/v1/documents``, ``GET /internal/v1/documents/{id}/versions``,
and ``POST /internal/v1/documents/{id}/versions/{version}/restore``. They run against
the in-process FastAPI app with the fake JWT verifier (see ``conftest.py``); all reads
are user-scoped by RLS so another user's document is never visible.
"""

from uuid import uuid4

from tests.planning.conftest import TEST_INTERNAL_TOKEN

# --- GET /internal/v1/documents ---------------------------------------------


async def test_list_documents_requires_internal_token(client, repository, user_context):
    await repository.create_active(user_context, type="task", title="t", body="b")
    resp = client.get(
        "/internal/v1/documents",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_list_documents_rejects_invalid_type(client, api_headers):
    resp = client.get("/internal/v1/documents", headers=api_headers, params={"type": "bogus"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_list_documents_returns_decrypted_documents(client, repository, user_context, api_headers):
    await repository.create_active(user_context, type="task", title="original", body="model")
    resp = client.get("/internal/v1/documents", headers=api_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["type"] == "task"
    assert body[0]["title"] == "original"
    assert body[0]["body"] == "model"
    assert body[0]["version"] == 1
    assert body[0]["createdAt"]
    assert body[0]["updatedAt"]


async def test_list_documents_filters_by_type(client, repository, user_context, api_headers):
    await repository.create_active(user_context, type="task", title="t", body="b")
    await repository.create_active(user_context, type="memory", title="m", body="b")
    resp = client.get("/internal/v1/documents", headers=api_headers, params={"type": "memory"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["type"] == "memory"
    assert body[0]["title"] == "m"


# --- GET /internal/v1/documents/{id}/versions --------------------------------


async def test_list_versions_returns_version_rows(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    await repository.update_active(user_context, doc.id, title="v2", body="two")
    resp = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["documentId"] == str(doc.id)
    assert body[0]["version"] == 1
    assert body[0]["title"] == "v1"
    assert body[0]["body"] == "one"
    assert body[1]["version"] == 2
    assert body[1]["title"] == "v2"
    assert body[1]["body"] == "two"
    assert body[0]["createdAt"]


async def test_list_versions_unknown_document_404(client, api_headers):
    resp = client.get(f"/internal/v1/documents/{uuid4()}/versions", headers=api_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- POST /internal/v1/documents/{id}/versions/{version}/restore -------------


async def test_restore_version_switches_current_version(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    await repository.update_active(user_context, doc.id, title="v2", body="two")
    versions = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers).json()
    first_version = versions[0]

    resp = client.post(
        f"/internal/v1/documents/{doc.id}/versions/{first_version['id']}/restore",
        headers=api_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # Restore SWITCHES current_version to the target; it does not create a new version.
    assert body["type"] == "task"
    assert body["version"] == 1
    assert body["title"] == "v1"
    assert body["body"] == "one"
    # Version count is unchanged (v1 + v2 still exist; current is now v1).
    remaining = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers).json()
    assert len(remaining) == 2
    assert body["createdAt"]
    assert body["updatedAt"]


async def test_restore_version_unknown_document_404(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    resp = client.post(
        f"/internal/v1/documents/{doc.id}/versions/{uuid4()}/restore",
        headers=api_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- RLS: another user's documents are never visible -------------------------


async def test_document_routes_are_user_scoped(client, repository, user_a, user_b):
    doc = await repository.create_active(user_a, type="task", title="private", body="secret")
    headers_b = {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_b.user_id}",
    }

    listed = client.get("/internal/v1/documents", headers=headers_b)
    assert listed.status_code == 200
    assert listed.json() == []

    versions = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=headers_b)
    assert versions.status_code == 404
    assert versions.json()["error"]["code"] == "NOT_FOUND"

    restored = client.post(
        f"/internal/v1/documents/{doc.id}/versions/{uuid4()}/restore",
        headers=headers_b,
    )
    assert restored.status_code == 404
    assert restored.json()["error"]["code"] == "NOT_FOUND"


# --- DELETE document ----------------------------------------------------------


async def test_delete_document_removes_owned_document(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="to-delete", body="body")
    resp = client.delete(f"/internal/v1/documents/{doc.id}", headers=api_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert await repository.get(user_context, doc.id) is None


async def test_delete_document_writes_audit(client, repository, user_context, api_headers, db_session):
    from sqlalchemy import text

    doc = await repository.create_active(user_context, type="task", title="audit-me", body="body")
    resp = client.delete(f"/internal/v1/documents/{doc.id}", headers=api_headers)
    assert resp.status_code == 200, resp.text

    count = await db_session.scalar(
        text("select count(*) from audit_logs where action = 'document.deleted' and user_id = :uid"),
        {"uid": str(user_context.user_id)},
    )
    assert count == 1


async def test_delete_document_not_owned_returns_404(client, repository, user_a, user_b):
    doc = await repository.create_active(user_a, type="task", title="private", body="secret")
    headers_b = {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_b.user_id}",
    }
    resp = client.delete(f"/internal/v1/documents/{doc.id}", headers=headers_b)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- PUT edit + DELETE version ------------------------------------------------


async def test_update_document_appends_new_version(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    resp = client.put(
        f"/internal/v1/documents/{doc.id}",
        headers={**api_headers, "Content-Type": "application/json"},
        json={"title": "v2", "body": "two"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2
    assert resp.json()["title"] == "v2"
    assert resp.json()["body"] == "two"


async def test_update_document_requires_title_and_body(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    resp = client.put(
        f"/internal/v1/documents/{doc.id}",
        headers={**api_headers, "Content-Type": "application/json"},
        json={"title": "only-title"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_delete_non_current_version_succeeds(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    await repository.update_active(user_context, doc.id, title="v2", body="two")
    versions = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers).json()
    # versions[0] is v1 (non-current)
    resp = client.delete(f"/internal/v1/documents/{doc.id}/versions/{versions[0]['id']}", headers=api_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    remaining = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers).json()
    assert len(remaining) == 1
    assert remaining[0]["version"] == 2


async def test_delete_current_version_is_rejected(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="v1", body="one")
    await repository.update_active(user_context, doc.id, title="v2", body="two")
    versions = client.get(f"/internal/v1/documents/{doc.id}/versions", headers=api_headers).json()
    current = next(v for v in versions if v["version"] == 2)
    resp = client.delete(f"/internal/v1/documents/{doc.id}/versions/{current['id']}", headers=api_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_update_document_not_owned_returns_404(client, repository, user_a, user_b):
    doc = await repository.create_active(user_a, type="task", title="private", body="secret")
    headers_b = {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_b.user_id}",
    }
    resp = client.put(
        f"/internal/v1/documents/{doc.id}",
        headers={**headers_b, "Content-Type": "application/json"},
        json={"title": "hijack", "body": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
