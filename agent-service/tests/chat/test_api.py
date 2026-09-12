"""API tests for durable SSE run streaming through POST /internal/v1/sessions/{id}/runs.

The tests run against the in-process FastAPI app with a deterministic provider and a
fake JWT verifier, so no network calls are made. Streaming is exercised via
``TestClient.stream``; ending the stream early simulates a client disconnect.
"""

import asyncio
import json
import uuid
from datetime import timedelta

from app.chat.provider import DeterministicProvider
from app.chat.service import ChatService
from sqlalchemy import text

from conftest import _iter_sse, _parse_sse

# Must match the deterministic provider's default text in app/chat/provider.py.
FAKE_PROVIDER_TEXT = "Hello from the Goudan agent!"
# How many message.delta events a single generation persists: the default provider
# emits the text in chunks of 8, so this is len(text) // 8 rounded up.
GENERATION_DELTA_COUNT = 4


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


async def test_reconnect_resumes_run_without_second_user_message(client, owned_session, api_headers, db_session):
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


async def test_concurrent_same_key_streams_persist_single_generation(
    chat_repository, assistant_graph, user_context, owned_session, db_session
):
    """Two overlapping same-key stream_run calls must never double-generate.

    Both consumers start on the same queued run; at most one may own generation, so
    the persisted stream holds exactly one ``run.started`` and one set of deltas.
    """
    service = ChatService(chat_repository, DeterministicProvider(), timedelta(days=7), graph=assistant_graph)
    created = await service.create_run(user_context, owned_session, "race", "race-key")
    run_id = created.run_id

    async def consume(agen):
        return [e async for e in agen]

    await asyncio.gather(
        consume(service.stream_run(user_context, run_id, 0)),
        consume(service.stream_run(user_context, run_id, 0)),
    )

    rows = (
        await db_session.execute(
            text("select kind from stream_events where run_id = :rid order by sequence"),
            {"rid": run_id},
        )
    ).all()
    kinds = [row[0] for row in rows]
    assert kinds.count("run.started") == 1
    assert kinds.count("message.delta") == GENERATION_DELTA_COUNT
    assert kinds.count("run.completed") == 1
    assert kinds[0] == "run.started" and kinds[-1] == "run.completed"

    status = await db_session.scalar(
        text("select status from messages where run_id = :rid and role = 'assistant'"),
        {"rid": run_id},
    )
    assert status == "completed"


def test_second_same_key_run_returns_existing_generation(client, owned_session, api_headers, db_session):
    """A second same-key POST after the first run finishes never starts a new generation.

    The replayed run must hold exactly one generation — one ``run.started`` and one set
    of deltas — even though the same idempotency key was used twice.
    """
    path = f"/internal/v1/sessions/{owned_session}/runs"
    body = {"content": "hello", "idempotency_key": "same-key-twice"}

    with client.stream("POST", path, headers=api_headers, json=body) as resp:
        assert resp.status_code == 200, resp.text
        first = _parse_sse(resp.iter_lines())
    with client.stream("POST", path, headers=api_headers, json=body) as resp:
        assert resp.status_code == 200, resp.text
        second = _parse_sse(resp.iter_lines())

    assert first[-1].kind == "run.completed"
    # Both connections saw the same run; the second was a pure replay, so the events
    # it received are a subset of the first's (no second run.started, no extra deltas).
    first_ids = {e.id for e in first}
    assert all(e.id in first_ids for e in second)
    assert len([e for e in first if e.kind == "run.started"]) == 1
    assert len([e for e in first if e.kind == "message.delta"]) == GENERATION_DELTA_COUNT


# --- POST /internal/v1/sessions (create session) ---


async def test_create_session_returns_owned_session(client, api_headers, user_context):
    resp = client.post("/internal/v1/sessions", headers=api_headers, json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["userId"] == str(user_context.user_id)
    assert body["title"] == "New chat"
    # The created session is listable by the caller.
    listed = client.get("/internal/v1/sessions", headers=api_headers).json()
    assert body["id"] in [s["id"] for s in listed]


async def test_create_session_accepts_empty_body(client, api_headers, user_context):
    """The frontend createSession() POSTs with NO body; it must not 422."""
    resp = client.post("/internal/v1/sessions", headers=api_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "New chat"


async def test_create_session_rejects_malformed_body(client, api_headers):
    resp = client.post(
        "/internal/v1/sessions",
        headers={**api_headers, "Content-Type": "application/json"},
        content=b"not-json",
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_session_accepts_custom_title(client, api_headers, user_context):
    resp = client.post("/internal/v1/sessions", headers=api_headers, json={"title": "My plan"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "My plan"


async def test_create_session_requires_internal_token(client, user_context):
    resp = client.post(
        "/internal/v1/sessions",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
        json={},
    )
    assert resp.status_code == 401


async def test_delete_session_removes_session_and_cascade(
    client, api_headers, user_context, chat_repository, owned_session, db_session
):
    # Seed a run + message so the cascade is exercised.
    created = await chat_repository.create_run(user_context, owned_session, "hello", "del-key")
    await chat_repository.append_event(
        created.run_id, "run.started", json.dumps({"messageId": str(created.assistant_message_id)})
    )

    resp = client.delete(f"/internal/v1/sessions/{owned_session}", headers=api_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    # Session, its runs, messages, and events are gone.
    listed = client.get("/internal/v1/sessions", headers=api_headers).json()
    assert owned_session not in [s["id"] for s in listed]
    rows = await db_session.execute(
        text("select count(*) from agent_runs where session_id = :sid"),
        {"sid": str(owned_session)},
    )
    assert rows.scalar_one() == 0


async def test_delete_session_not_owned_returns_404(client, api_headers, user_b_context, db_session):
    # A session owned by another user is invisible under RLS -> 404.
    sid = uuid.uuid4()
    await db_session.execute(
        text("insert into sessions (id, user_id, title) values (:id, :uid, 'other')"),
        {"id": sid, "uid": user_b_context.user_id},
    )
    await db_session.commit()
    resp = client.delete(f"/internal/v1/sessions/{sid}", headers=api_headers)
    assert resp.status_code == 404


async def test_delete_session_requires_internal_token(client, owned_session, user_context):
    resp = client.delete(
        f"/internal/v1/sessions/{owned_session}",
        headers={"Authorization": f"Bearer {user_context.user_id}"},
    )
    assert resp.status_code == 401


async def test_rename_session_updates_title(client, api_headers, user_context, owned_session):
    resp = client.patch(
        f"/internal/v1/sessions/{owned_session}",
        headers={**api_headers, "Content-Type": "application/json"},
        json={"title": "兴趣·学摄影"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    listed = client.get("/internal/v1/sessions", headers=api_headers).json()
    titles = {s["id"]: s["title"] for s in listed}
    assert titles.get(str(owned_session)) == "兴趣·学摄影"


async def test_rename_session_requires_title(client, api_headers, owned_session):
    resp = client.patch(
        f"/internal/v1/sessions/{owned_session}",
        headers={**api_headers, "Content-Type": "application/json"},
        json={},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_rename_session_not_owned_returns_404(client, api_headers, user_b_context, db_session):
    sid = uuid.uuid4()
    await db_session.execute(
        text("insert into sessions (id, user_id, title) values (:id, :uid, 'other')"),
        {"id": sid, "uid": user_b_context.user_id},
    )
    await db_session.commit()
    resp = client.patch(
        f"/internal/v1/sessions/{sid}",
        headers={**api_headers, "Content-Type": "application/json"},
        json={"title": "hijack"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_create_session_binds_employee(client, api_headers, user_context, db_session, cipher, chat_repository):
    """POST /sessions with employee_id creates an employee-bound session."""
    # The employee FK references auth.users(id); provision the caller first.
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_context.user_id},
    )
    await db_session.commit()

    from app.repositories.employees import EmployeeRepository

    employee_repo = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repo.create(user_context, "苏曼", "秘书", "负责安排日程与会议纪要")

    resp = client.post(
        "/internal/v1/sessions",
        headers={**api_headers, "Content-Type": "application/json"},
        json={"title": "[秘书]苏曼", "employee_id": str(employee.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "[秘书]苏曼"

    bound = await chat_repository.get_session_employee_id(user_context, uuid.UUID(body["id"]))
    assert bound == employee.id
