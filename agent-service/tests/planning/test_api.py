"""API tests for POST /internal/v1/approvals/{id}/decisions.

The tests run against the in-process FastAPI app with a fake JWT verifier, so no
network calls are made. The internal-token gate mirrors the chat router, and the
end-user JWT is decoded from a ``Bearer <uuid>`` token into the request context.
"""


from sqlalchemy import text


async def test_decide_requires_internal_token(client, service, user_context, api_headers):
    draft = await _create_draft(service, user_context)
    resp = client.post(
        f"/internal/v1/approvals/{draft.approval_id}/decisions",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={"decision": "approve", "idempotency_key": "api-1"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_decide_rejects_missing_idempotency_key(client, api_headers):
    resp = client.post(
        "/internal/v1/approvals/00000000-0000-0000-0000-000000000000/decisions",
        headers=api_headers,
        json={"decision": "approve"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_decide_rejects_invalid_decision(client, api_headers):
    resp = client.post(
        "/internal/v1/approvals/00000000-0000-0000-0000-000000000000/decisions",
        headers=api_headers,
        json={"decision": "maybe", "idempotency_key": "api-2"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_decide_rejects_malformed_edited_payload(client, service, user_context, api_headers):
    draft = await _create_draft(service, user_context)
    resp = client.post(
        f"/internal/v1/approvals/{draft.approval_id}/decisions",
        headers=api_headers,
        json={"decision": "approve", "edited_payload": {"title": 123}, "idempotency_key": "api-3"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_decide_approve_returns_created_document(client, service, user_context, api_headers):
    draft = await _create_draft(service, user_context)
    resp = client.post(
        f"/internal/v1/approvals/{draft.approval_id}/decisions",
        headers=api_headers,
        json={"decision": "approve", "idempotency_key": "api-4"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approvalId"] == str(draft.approval_id)
    assert body["decision"] == "confirmed"
    assert body["version"] == 1
    assert body["originalPayload"]["title"] == "original"
    assert body["document"]["title"] == "original"
    assert body["document"]["version"] == 1


async def test_decide_edit_confirm_returns_edited_document(client, service, user_context, api_headers):
    draft = await _create_draft(service, user_context)
    resp = client.post(
        f"/internal/v1/approvals/{draft.approval_id}/decisions",
        headers=api_headers,
        json={
            "decision": "approve",
            "edited_payload": {"title": "edited", "body": "user"},
            "idempotency_key": "api-5",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["document"]["title"] == "edited"
    assert body["document"]["body"] == "user"
    assert body["originalPayload"]["title"] == "original"


async def test_decide_repeated_key_returns_original_result(
    client, service, user_context, api_headers, db_session
):
    draft = await _create_draft(service, user_context)
    path = f"/internal/v1/approvals/{draft.approval_id}/decisions"
    body = {"decision": "approve", "idempotency_key": "api-repeat"}
    first = client.post(path, headers=api_headers, json=body)
    second = client.post(path, headers=api_headers, json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    first_json = first.json()
    second_json = second.json()
    assert second_json["approvalId"] == first_json["approvalId"]
    assert second_json["document"]["version"] == first_json["document"]["version"]
    versions = await db_session.scalar(text("select count(*) from document_versions"))
    assert versions == 1


async def test_decide_conflicting_key_returns_409(client, service, user_context, api_headers):
    draft = await _create_draft(service, user_context)
    path = f"/internal/v1/approvals/{draft.approval_id}/decisions"
    first = client.post(path, headers=api_headers, json={"decision": "approve", "idempotency_key": "api-6"})
    assert first.status_code == 200
    conflict = client.post(path, headers=api_headers, json={"decision": "approve", "idempotency_key": "api-7"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "APPROVAL_CONFLICT"


async def test_decide_reject_returns_no_document(client, service, user_context, api_headers, db_session):
    draft = await _create_draft(service, user_context)
    resp = client.post(
        f"/internal/v1/approvals/{draft.approval_id}/decisions",
        headers=api_headers,
        json={"decision": "reject", "idempotency_key": "api-8"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["document"] is None
    assert body["decision"] == "rejected"
    documents = await db_session.scalar(text("select count(*) from documents"))
    assert documents == 0


async def _create_draft(service, user_context):
    return await service.create_document_draft(
        user_context, type="task", title="original", body="model"
    )
