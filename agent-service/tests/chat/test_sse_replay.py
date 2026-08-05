"""SSE replay tests: GET /internal/v1/runs/{id}/events replays persisted events after a cursor.

Replay reads are RLS-gated (a user can only replay their own run's events), and event
purge is bounded by the configured retention period and limited to terminal runs.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


async def test_sse_replays_only_events_after_last_id(client, seeded_run, collect_sse):
    first = await collect_sse(client, f"/internal/v1/runs/{seeded_run}/events")
    replay = await collect_sse(client, f"/internal/v1/runs/{seeded_run}/events", last_event_id=first[0].id)
    assert [event.id for event in replay] == [event.id for event in first[1:]]


async def test_sse_replay_from_zero_returns_all_events(client, seeded_run, collect_sse):
    events = await collect_sse(client, f"/internal/v1/runs/{seeded_run}/events")
    assert [e.kind for e in events] == [
        "run.started",
        "message.delta",
        "message.delta",
        "message.delta",
        "run.completed",
    ]


async def test_sse_replay_rejects_run_not_owned_by_caller(client, seeded_run, user_b_context):
    resp = client.get(
        f"/internal/v1/runs/{seeded_run}/events",
        headers={
            "X-Internal-Token": "test-internal-token",
            "Authorization": f"Bearer {user_b_context.user_id}",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_purge_expired_events_erases_terminal_run_events_after_retention(
    chat_repository, user_context, db_session
):
    created = await chat_repository.create_run(user_context, uuid.uuid4(), "hi", "purge-1")
    await chat_repository.append_event(created.run_id, "message.delta", '{"text":"a"}')
    await chat_repository.update_run_status(user_context, created.run_id, "completed")

    await db_session.execute(
        text("update stream_events set created_at = :t"),
        {"t": datetime.now(UTC) - timedelta(days=8)},
    )
    await db_session.commit()

    deleted = await chat_repository.purge_expired_events(datetime.now(UTC) - timedelta(days=7))
    assert deleted == 1


async def test_purge_expired_events_skips_non_terminal_runs(chat_repository, user_context, db_session):
    created = await chat_repository.create_run(user_context, uuid.uuid4(), "hi", "purge-2")
    await chat_repository.append_event(created.run_id, "message.delta", '{"text":"a"}')
    # Leave the run 'queued' (non-terminal): its events must survive the purge.
    await db_session.execute(
        text("update stream_events set created_at = :t"),
        {"t": datetime.now(UTC) - timedelta(days=8)},
    )
    await db_session.commit()

    deleted = await chat_repository.purge_expired_events(datetime.now(UTC) - timedelta(days=7))
    assert deleted == 0
