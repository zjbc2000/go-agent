"""API tests for POST /internal/v1/approvals (create a proposal draft).

The route validates the type/title/body plus any optional ``document_id``/``run_id``,
then creates a pending approval via ``PlanningService.create_document_draft``. Drafting
an edit of another user's document surfaces as NOT_FOUND (RLS).
"""

from sqlalchemy import text


async def test_create_draft_requires_internal_token(client, user_context):
    resp = client.post(
        "/internal/v1/approvals",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={"type": "task", "title": "plan", "body": "details"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_create_draft_creates_pending_approval(client, api_headers, db_session):
    resp = client.post(
        "/internal/v1/approvals",
        headers=api_headers,
        json={"type": "task", "title": "plan", "body": "details"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approvalId"]
    assert body["draftId"]
    assert body["documentId"] is None
    assert body["type"] == "task"
    assert body["title"] == "plan"
    assert body["body"] == "details"

    drafts = await db_session.scalar(text("select count(*) from document_drafts"))
    approvals = await db_session.scalar(text("select count(*) from approvals"))
    assert drafts == 1
    assert approvals == 1


async def test_create_draft_rejects_invalid_type(client, api_headers):
    resp = client.post(
        "/internal/v1/approvals",
        headers=api_headers,
        json={"type": "bogus", "title": "plan", "body": "details"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_draft_rejects_invalid_document_id(client, api_headers):
    resp = client.post(
        "/internal/v1/approvals",
        headers=api_headers,
        json={"type": "task", "title": "plan", "body": "details", "document_id": "not-a-uuid"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_draft_links_owned_document(client, repository, user_context, api_headers):
    doc = await repository.create_active(user_context, type="task", title="original", body="body")
    resp = client.post(
        "/internal/v1/approvals",
        headers=api_headers,
        json={
            "type": "task",
            "title": "edited",
            "body": "new",
            "document_id": str(doc.id),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["documentId"] == str(doc.id)
    assert resp.json()["title"] == "edited"


async def test_create_draft_other_users_document_404(client, repository, user_a, api_headers):
    doc = await repository.create_active(user_a, type="task", title="private", body="secret")
    resp = client.post(
        "/internal/v1/approvals",
        headers=api_headers,
        json={"type": "task", "title": "plan", "body": "details", "document_id": str(doc.id)},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
