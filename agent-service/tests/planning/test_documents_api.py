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


async def test_list_documents_returns_decrypted_documents(
    client, repository, user_context, api_headers
):
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


async def test_restore_version_returns_new_current_document(
    client, repository, user_context, api_headers
):
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
    assert body["type"] == "task"
    assert body["version"] == 3
    assert body["title"] == "v1"
    assert body["body"] == "one"
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
