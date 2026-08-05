"""Fixtures for chat persistence and API tests against the local Supabase Postgres.

The repository runs every user-scoped transaction as the ``authenticated`` role
with ``request.jwt.claims`` derived from the ``RequestContext``, so RLS policies
evaluate against the caller. This fixture module only wires up the connection;
see ``app/db/session.py`` for the transaction boundary.
"""

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from app.api.deps import decode_request_context
from app.chat.deps import get_request_context
from app.core.config import Settings
from app.core.context import RequestContext, UserRole, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError
from app.main import create_app
from app.repositories.chat import ChatRepository
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

TEST_INTERNAL_TOKEN = "test-internal-token"

# Local Supabase Postgres (see `supabase status`). Override with TEST_DATABASE_URL
# to point at a different database.
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

_CHAT_TABLES = "public.stream_events, public.messages, public.agent_runs, public.sessions"


def _context(user_id: uuid.UUID) -> RequestContext:
    return RequestContext(user_id=user_id, role="user", request_id=new_request_id())


@pytest.fixture
def engine() -> AsyncEngine:
    """A fresh async engine per test so asyncpg connections never cross event loops."""
    return create_async_engine(DATABASE_URL, pool_pre_ping=True)


@pytest.fixture(autouse=True)
async def _clean_chat_tables(engine: AsyncEngine) -> None:
    """Truncate chat tables before each test for a deterministic starting state."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(text(f"truncate table {_CHAT_TABLES} cascade"))
        await session.commit()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An unscoped session (superuser) for inspecting raw stored columns."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def chat_repository(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> ChatRepository:
    return ChatRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher
    )


@pytest.fixture
def assistant_graph(engine: AsyncEngine, cipher: LocalEnvelopeCipher):
    from app.agent.graph import build_assistant_graph
    from app.chat.provider import DeterministicProvider
    from app.repositories.planning import DocumentRepository

    documents = DocumentRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher
    )
    return build_assistant_graph(DeterministicProvider(delay_seconds=0.0), documents)


@pytest.fixture
def user_context() -> RequestContext:
    return _context(uuid.uuid4())


@pytest.fixture
def user_a_context() -> RequestContext:
    return _context(uuid.uuid4())


@pytest.fixture
def user_b_context() -> RequestContext:
    return _context(uuid.uuid4())


# --- API fixtures ------------------------------------------------------------

@dataclass(frozen=True)
class SseEvent:
    """A parsed named SSE event from an API stream."""

    id: int
    kind: str
    data: str


def _iter_sse(lines: Iterator[str]) -> Iterator[SseEvent]:
    current = None
    for line in lines:
        line = line.strip()
        if line.startswith("id:"):
            current = SseEvent(id=int(line[3:].strip()), kind="", data="")
        elif line.startswith("event:") and current is not None:
            current = SseEvent(id=current.id, kind=line[6:].strip(), data=current.data)
        elif line.startswith("data:") and current is not None:
            current = SseEvent(id=current.id, kind=current.kind, data=line[5:].strip())
        elif line == "" and current is not None:
            yield current
            current = None


def _parse_sse(lines: Iterator[str]) -> list[SseEvent]:
    return list(_iter_sse(lines))


def _fake_jwt_verifier(token: str) -> dict:
    """Verify a test token of the form ``Bearer <uuid>`` without any network call."""
    try:
        user_id = uuid.UUID(token)
    except (ValueError, TypeError):
        raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None
    return {"sub": str(user_id)}


def _fake_role_loader(user_id: uuid.UUID) -> UserRole:
    return "user"


@pytest.fixture
def app_settings() -> Settings:
    # Disable the outbox poller so entering the TestClient never starts the
    # RabbitMQ background task during unrelated suites.
    return Settings(internal_token=TEST_INTERNAL_TOKEN, outbox_poller_enabled=False)


@pytest.fixture
def test_app(app_settings: Settings):
    """A FastAPI app with the user-JWT dependency replaced by a fake verifier."""
    app = create_app(settings=app_settings)

    def fake_context(authorization: str | None = Header(None)) -> RequestContext:
        return decode_request_context(
            authorization,
            jwt_verifier=_fake_jwt_verifier,
            role_loader=_fake_role_loader,
        )

    app.dependency_overrides[get_request_context] = fake_context
    return app


@pytest.fixture
def client(test_app):
    # Entering the TestClient keeps ONE portal/event loop for all requests in a test, so
    # the app's async engine pool never crosses event loops.
    with TestClient(test_app) as test_client:
        yield test_client


@pytest.fixture
def api_headers(user_context: RequestContext) -> dict[str, str]:
    return {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_context.user_id}",
    }


@pytest.fixture
async def owned_session(db_session: AsyncSession, user_context: RequestContext) -> uuid.UUID:
    """A session row owned by the default test user."""
    session_id = uuid.uuid4()
    await db_session.execute(
        text("insert into sessions (id, user_id, title) values (:id, :uid, 'Test session')"),
        {"id": session_id, "uid": user_context.user_id},
    )
    await db_session.commit()
    return session_id


@pytest.fixture
async def seeded_run(chat_repository: ChatRepository, user_context: RequestContext) -> str:
    """A completed run with a fixed set of replayed events (no provider involved)."""
    created = await chat_repository.create_run(user_context, uuid.uuid4(), "seed prompt", "seed-key")
    message_id = created.assistant_message_id
    await chat_repository.append_event(
        created.run_id, "run.started", json.dumps({"messageId": str(message_id), "runId": str(created.run_id)})
    )
    for delta_text in ("one", "two", "three"):
        await chat_repository.append_event(
            created.run_id,
            "message.delta",
            json.dumps({"messageId": str(message_id), "text": delta_text}),
        )
    await chat_repository.append_event(created.run_id, "run.completed", json.dumps({"messageId": str(message_id)}))
    await chat_repository.update_run_status(user_context, created.run_id, "completed")
    await chat_repository.finalize_message(user_context, message_id, "onetwothree")
    return str(created.run_id)


@pytest.fixture
def collect_sse(user_context: RequestContext):
    """Collect all SSE events from a GET replay endpoint, with test auth headers."""

    async def _collect(client, path: str, last_event_id: int | None = None, headers: dict | None = None):
        base = {
            "X-Internal-Token": TEST_INTERNAL_TOKEN,
            "Authorization": f"Bearer {user_context.user_id}",
        }
        if last_event_id is not None:
            base["Last-Event-ID"] = str(last_event_id)
        if headers:
            base.update(headers)
        with client.stream("GET", path, headers=base) as resp:
            assert resp.status_code == 200, resp.text
            return _parse_sse(resp.iter_lines())

    return _collect
