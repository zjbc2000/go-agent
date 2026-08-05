"""API tests for POST /internal/v1/skills/{document_id}/executions.

The tests run against the in-process FastAPI app with a fake JWT verifier, so no
network calls are made. The internal-token gate mirrors the planning/chat routers,
and the end-user JWT is decoded from a ``Bearer <uuid>`` token into the request
context. Responses carry safe fields only: no secrets, no ciphertext.
"""

import json

from sqlalchemy import text


async def test_write_skill_returns_approval_envelope(client, service, user_context, api_headers, active_skill):
    resp = client.post(
        f"/internal/v1/skills/{active_skill}/executions",
        headers=api_headers,
        json={"inputs": {"title": "x"}, "idempotency_key": "api-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "approval" in body
    assert body["approval"]["status"] == "pending"
    assert body["approval"]["id"]
    assert "expiresAt" in body["approval"]
    assert "createdAt" in body["approval"]
    assert "run" not in body


async def test_read_skill_returns_run_envelope(
    client, service, repository, user_context, api_headers, active_read_skill
):
    target = await repository.create_active(user_context, type="task", title="target", body="body")
    resp = client.post(
        f"/internal/v1/skills/{active_read_skill}/executions",
        headers=api_headers,
        json={"inputs": {"document_id": str(target.id)}, "idempotency_key": "api-2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "run" in body
    assert body["run"]["status"] == "queued"
    assert body["run"]["id"]
    assert body["run"]["planHash"]
    assert "approval" not in body


async def test_execution_requires_internal_token(client, active_skill, user_context):
    resp = client.post(
        f"/internal/v1/skills/{active_skill}/executions",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={"inputs": {"title": "x"}, "idempotency_key": "api-3"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_execution_rejects_missing_idempotency_key(client, api_headers, active_skill):
    resp = client.post(
        f"/internal/v1/skills/{active_skill}/executions",
        headers=api_headers,
        json={"inputs": {"title": "x"}},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_execution_rejects_blank_idempotency_key(client, api_headers, active_skill):
    resp = client.post(
        f"/internal/v1/skills/{active_skill}/executions",
        headers=api_headers,
        json={"inputs": {"title": "x"}, "idempotency_key": "   "},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_execution_rejects_non_dict_inputs(client, api_headers, active_skill):
    resp = client.post(
        f"/internal/v1/skills/{active_skill}/executions",
        headers=api_headers,
        json={"inputs": "oops", "idempotency_key": "api-4"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_execution_repeated_key_returns_same_approval(
    client, service, user_context, api_headers, active_skill, db_session
):
    path = f"/internal/v1/skills/{active_skill}/executions"
    body = {"inputs": {"title": "x"}, "idempotency_key": "api-repeat"}
    first = client.post(path, headers=api_headers, json=body)
    second = client.post(path, headers=api_headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["approval"]["id"] == first.json()["approval"]["id"]
    approvals = await db_session.scalar(text("select count(*) from execution_approvals"))
    assert approvals == 1


async def test_execution_cross_user_returns_404(client, repository, user_a, user_b):
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.create", "input": {"type": "task", "title": "x", "body": "b"}}],
    }
    skill = await repository.create_active(user_a, type="skill", title="a-skill", body=json.dumps(manifest))
    resp = client.post(
        f"/internal/v1/skills/{skill.id}/executions",
        headers={
            "X-Internal-Token": "test-internal-token",
            "Authorization": f"Bearer {user_b.user_id}",
        },
        json={"inputs": {"title": "x"}, "idempotency_key": "api-5"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
