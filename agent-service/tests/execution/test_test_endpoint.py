"""Test-mode approval-expiry endpoint tests.

``POST /internal/v1/test/expire-latest-approval`` exists ONLY to drive the
skill-execution E2E: it backdates the caller's newest pending execution
approval so the UI can prove an expired decision never queues a run. It is
gated behind ``AGENT_TEST_MODE=true`` (AUTHZ_DENIED 403 when the flag is off)
and is user-scoped (RLS), so a caller with no pending approval -- and a
cross-user caller -- sees NOT_FOUND (404). It never touches another user's
approvals.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
from app.api.deps import decode_request_context
from app.chat.deps import get_request_context
from app.core.config import Settings
from app.core.context import RequestContext, UserRole
from app.core.errors import ApiError
from app.main import create_app
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.execution.conftest import DATABASE_URL

TEST_INTERNAL_TOKEN = "test-internal-token"

EXPIRE_PATH = "/internal/v1/test/expire-latest-approval"


def _fake_jwt_verifier(token: str) -> dict:
    """Verify a test token of the form ``Bearer <uuid>`` without any network call."""
    try:
        return {"sub": str(uuid.UUID(token))}
    except (ValueError, TypeError):
        raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None


def _fake_role_loader(user_id: uuid.UUID) -> UserRole:
    return "user"


def _make_app(test_mode: bool) -> TestClient:
    """A TestClient over an app with ``AGENT_TEST_MODE`` set to ``test_mode``."""
    app = create_app(
        settings=Settings(
            internal_token=TEST_INTERNAL_TOKEN,
            outbox_poller_enabled=False,
            test_mode=test_mode,
            database_url=DATABASE_URL,
        )
    )

    def fake_context(authorization: str | None = Header(None)) -> RequestContext:
        return decode_request_context(
            authorization,
            jwt_verifier=_fake_jwt_verifier,
            role_loader=_fake_role_loader,
        )

    app.dependency_overrides[get_request_context] = fake_context
    return TestClient(app)


@pytest.fixture
def test_mode_client() -> TestClient:
    with _make_app(test_mode=True) as test_client:
        yield test_client


@pytest.fixture
def non_test_mode_client() -> TestClient:
    with _make_app(test_mode=False) as test_client:
        yield test_client


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_id}",
    }


async def test_expire_endpoint_requires_test_mode(non_test_mode_client, service, user_context, active_skill):
    """The endpoint is inert without AGENT_TEST_MODE=true (403, never 200)."""
    await service.request_execution(user_context, active_skill, {"title": "x"}, "req-gate")
    resp = non_test_mode_client.post(EXPIRE_PATH, headers=_headers(user_context.user_id))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTHZ_DENIED"


async def test_expire_endpoint_backdates_latest_pending_approval(
    test_mode_client, service, user_context, active_skill, db_session
):
    await service.request_execution(user_context, active_skill, {"title": "x"}, "req-1")
    resp = test_mode_client.post(EXPIRE_PATH, headers=_headers(user_context.user_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["approvalId"]
    assert body["status"] == "pending"
    row = (await db_session.execute(text("select status, expires_at from execution_approvals"))).one()
    assert row[0] == "pending"
    assert row[1] < datetime.now(UTC)


async def test_expire_endpoint_targets_only_latest_pending(
    test_mode_client, service, user_context, active_skill, db_session
):
    """With several pending approvals the NEWEST one is the one expired."""
    first = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-a")
    second = await service.request_execution(user_context, active_skill, {"title": "x"}, "req-b")
    assert first.approval is not None and second.approval is not None
    resp = test_mode_client.post(EXPIRE_PATH, headers=_headers(user_context.user_id))
    assert resp.status_code == 200
    assert resp.json()["approvalId"] == str(second.approval.id)
    rows = (await db_session.execute(text("select id, expires_at from execution_approvals order by created_at"))).all()
    assert rows[0][1] >= datetime.now(UTC)
    assert rows[1][1] < datetime.now(UTC)


async def test_expire_endpoint_no_pending_returns_404(test_mode_client, user_context):
    resp = test_mode_client.post(EXPIRE_PATH, headers=_headers(user_context.user_id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_expire_endpoint_cross_user_returns_404(test_mode_client, service, repository, user_a, user_b):
    """User B can never expire User A's approval: RLS hides it entirely."""
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.create", "input": {"type": "task", "title": "x", "body": "b"}}],
    }
    skill = await repository.create_active(user_a, type="skill", title="a-skill", body=json.dumps(manifest))
    await service.request_execution(user_a, skill.id, {"title": "x"}, "req-cross")
    resp = test_mode_client.post(EXPIRE_PATH, headers=_headers(user_b.user_id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_expire_endpoint_requires_internal_token(test_mode_client, user_context):
    resp = test_mode_client.post(EXPIRE_PATH, headers={"Authorization": f"Bearer {user_context.user_id}"})
    assert resp.status_code == 401
