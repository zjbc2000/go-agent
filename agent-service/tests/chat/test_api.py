"""API tests for durable SSE run streaming through POST /internal/v1/sessions/{id}/runs.

The tests run against the in-process FastAPI app with a deterministic provider and a
fake JWT verifier, so no network calls are made. Streaming is exercised via
``TestClient.stream``; ending the stream early simulates a client disconnect.
"""

import uuid

from sqlalchemy import text

from conftest import _iter_sse, _parse_sse

# Must match the deterministic provider's default text in app/chat/provider.py.
FAKE_PROVIDER_TEXT = "Hello from the Goudan agent!"


async def test_create_run_requires_internal_token(client, owned_session, user_context):
    resp = client.post(
        f"/internal/v1/sessions/{owned_session}/runs",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={"content": "hi", "idempotency_key": "k-1"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_create_run_rejects_missing_content(client, owned_session, api_headers):
    resp = client.post(
        f"/internal/v1/sessions/{owned_session}/runs",
        headers=api_headers,
        json={"idempotency_key": "k-1b"},
    )
    assert resp.status_code == 422


async def test_create_run_rejects_session_not_owned_by_caller(client, owned_session, user_b_context):
    resp = client.post(
        f"/internal/v1/sessions/{owned_session}/runs",
        headers={
            "X-Internal-Token": "test-internal-token",
            "Authorization": f"Bearer {user_b_context.user_id}",
        },
        json={"content": "hi", "idempotency_key": "k-2"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_list_sessions_returns_only_owned_sessions(client, owned_session, api_headers, db_session):
    other_id = uuid.uuid4()
    await db_session.execute(
        text("insert into sessions (id, user_id, title) values (:id, :uid, 'Other')"),
        {"id": other_id, "uid": uuid.uuid4()},
    )
    await db_session.commit()

    resp = client.get("/internal/v1/sessions", headers=api_headers)
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert str(owned_session) in ids
    assert str(other_id) not in ids


async def test_stream_run_persists_events_and_finalizes_message(
    client, owned_session, api_headers, chat_repository, user_context
):
    with client.stream(
        "POST",
        f"/internal/v1/sessions/{owned_session}/runs",
        headers=api_headers,
        json={"content": "hello", "idempotency_key": "k-3"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        events = _parse_sse(resp.iter_lines())

    kinds = [e.kind for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"
    assert all(k == "message.delta" for k in kinds[1:-1])

    messages = await chat_repository.list_messages(user_context, owned_session)
    assistant = [m for m in messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].status == "completed"
    assert assistant[0].content == FAKE_PROVIDER_TEXT


async def test_stream_deltas_are_encrypted_at_rest(client, owned_session, api_headers, db_session):
    with client.stream(
        "POST",
        f"/internal/v1/sessions/{owned_session}/runs",
        headers=api_headers,
        json={"content": "hello", "idempotency_key": "k-4"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        list(_parse_sse(resp.iter_lines()))

    # Every inserted message row (user + assistant) holds ciphertext, never plaintext.
    raw_messages = (await db_session.execute(text("select content_ciphertext from messages"))).all()
    assert len(raw_messages) == 2
    assert all("hello" not in row[0] for row in raw_messages)

    # Every inserted stream-event row holds ciphertext, never a provider delta.
    raw_events = (await db_session.execute(text("select payload_ciphertext from stream_events"))).all()
    assert all(FAKE_PROVIDER_TEXT[:8] not in row[0] for row in raw_events)


async def test_reconnect_resumes_run_without_second_user_message(
    client, owned_session, api_headers, db_session
):
    path = f"/internal/v1/sessions/{owned_session}/runs"
    body = {"content": "hello", "idempotency_key": "k-5"}

    # First connection: read two events then disconnect.
    first_ids = []
    with client.stream("POST", path, headers=api_headers, json=body) as resp:
        assert resp.status_code == 200, resp.text
        iterator = _iter_sse(resp.iter_lines())
        for _ in range(2):
            first_ids.append(next(iterator).id)

    # Reconnect with the same idempotency key and the Last-Event-ID cursor.
    reconnected = []
    with client.stream(
        "POST",
        path,
        headers={**api_headers, "Last-Event-ID": str(first_ids[-1])},
        json=body,
    ) as resp:
        assert resp.status_code == 200, resp.text
        reconnected = _parse_sse(resp.iter_lines())

    # The resumed stream never re-delivers an event the client already saw.
    all_ids = first_ids + [e.id for e in reconnected]
    assert len(all_ids) == len(set(all_ids)), f"duplicate event ids in {all_ids}"
    assert reconnected[-1].kind == "run.completed"

    # Exactly one user message is persisted across both connections.
    user_count = (await db_session.execute(text("select count(*) from messages where role = 'user'"))).scalar()
    assert user_count == 1
