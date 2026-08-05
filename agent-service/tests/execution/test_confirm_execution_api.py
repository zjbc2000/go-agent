"""API tests for POST /internal/v1/skills/approvals/{approval_id}/decisions.

Confirms a pending execution approval (approve only), queues its sandbox run in
one user-scoped transaction, and surfaces APPROVAL_EXPIRED / APPROVAL_CONFLICT /
NOT_FOUND as typed errors. This is the HTTP surface the skill-execution UI's
"确认执行" button calls.
"""

from sqlalchemy import text


async def test_confirm_pending_approval_queues_run(
    client, service, user_context, api_headers, active_skill, db_session
):
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-1")
    assert result.approval is not None
    resp = client.post(
        f"/internal/v1/skills/approvals/{result.approval.id}/decisions",
        headers=api_headers,
        json={"decision": "approve", "idempotency_key": "confirm-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "confirmed"
    assert body["run"]["status"] == "queued"
    assert body["run"]["planHash"] == result.approval.plan_hash
    runs = await db_session.scalar(text("select count(*) from sandbox_runs"))
    assert runs == 1


async def test_confirm_expired_approval_returns_approval_expired(
    client, service, user_context, api_headers, active_skill, db_session
):
    """The E2E's expired-decision path: a backdated approval never queues a run."""
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-2")
    assert result.approval is not None
    await db_session.execute(
        text("update execution_approvals set expires_at = now() - interval '1 minute' where id = :id"),
        {"id": result.approval.id},
    )
    await db_session.commit()
    resp = client.post(
        f"/internal/v1/skills/approvals/{result.approval.id}/decisions",
        headers=api_headers,
        json={"decision": "approve", "idempotency_key": "confirm-2"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "APPROVAL_EXPIRED"
    runs = await db_session.scalar(text("select count(*) from sandbox_runs"))
    assert runs == 0


async def test_confirm_repeated_key_is_idempotent(
    client, service, user_context, api_headers, active_skill, db_session
):
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-3")
    assert result.approval is not None
    path = f"/internal/v1/skills/approvals/{result.approval.id}/decisions"
    body = {"decision": "approve", "idempotency_key": "confirm-3"}
    first = client.post(path, headers=api_headers, json=body)
    second = client.post(path, headers=api_headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["run"]["id"] == first.json()["run"]["id"]
    runs = await db_session.scalar(text("select count(*) from sandbox_runs"))
    assert runs == 1


async def test_confirm_rejects_non_approve_decision(
    client, service, user_context, api_headers, active_skill
):
    """Execution approvals have no reject/regenerate path in this MVP."""
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-4")
    assert result.approval is not None
    resp = client.post(
        f"/internal/v1/skills/approvals/{result.approval.id}/decisions",
        headers=api_headers,
        json={"decision": "reject", "idempotency_key": "confirm-4"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_confirm_unknown_approval_returns_404(client, api_headers):
    resp = client.post(
        "/internal/v1/skills/approvals/00000000-0000-0000-0000-000000000000/decisions",
        headers=api_headers,
        json={"decision": "approve", "idempotency_key": "confirm-5"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_confirm_requires_internal_token(client, service, user_context, active_skill):
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-6")
    assert result.approval is not None
    resp = client.post(
        f"/internal/v1/skills/approvals/{result.approval.id}/decisions",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={"decision": "approve", "idempotency_key": "confirm-6"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
