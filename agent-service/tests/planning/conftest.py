"""Fixtures for planning repository and RLS tests against the local Supabase Postgres.

The repository runs every user-scoped transaction as the ``authenticated`` role
with ``request.jwt.claims`` derived from the ``RequestContext``, so RLS policies
evaluate against the caller. ``documents.user_id`` references ``auth.users(id)``,
so each synthetic test user is provisioned a matching row before the test body runs.
"""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from app.api.deps import decode_request_context
from app.chat.deps import get_request_context
from app.chat.provider import DeterministicProvider
from app.chat.service import ChatService
from app.core.config import Settings
from app.core.context import RequestContext, UserRole, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError
from app.main import create_app
from app.planning.service import PlanningService
from app.repositories.chat import ChatRepository
from app.repositories.planning import DocumentRepository
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

TEST_INTERNAL_TOKEN = "test-internal-token"

# Local Supabase Postgres (see `supabase status`). Override with TEST_DATABASE_URL
# to point at a different database.
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

_PLANNING_TABLES = (
    "public.audit_logs, public.approvals, public.document_drafts, public.document_versions, public.documents"
)

# The test suite TRUNCATEs planning tables before each test. That is safe ONLY against
# an isolated test database. If TEST_DATABASE_URL is not set and the default points at
# the dev database (`/postgres`), refuse to run — otherwise the suite silently wipes
# the developer's real documents. Set TEST_DATABASE_URL to an isolated test DB.
if os.getenv("TEST_DATABASE_URL") is None and DATABASE_URL.rstrip("/").endswith("/postgres"):
    raise RuntimeError(
        "Refusing to run tests against the dev database. Set TEST_DATABASE_URL to an "
        "isolated test database (e.g. postgres_test)."
    )


def _context(user_id: uuid.UUID) -> RequestContext:
    return RequestContext(user_id=user_id, role="user", request_id=new_request_id())


async def _provision_user(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    """Insert a minimal auth.users row so the ``documents.user_id`` FK is satisfied."""
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_id},
    )
    await db_session.commit()


@pytest.fixture
def engine() -> AsyncEngine:
    """A fresh async engine per test so asyncpg connections never cross event loops."""
    return create_async_engine(DATABASE_URL, pool_pre_ping=True)


@pytest.fixture(autouse=True)
async def _clean_planning_tables(engine: AsyncEngine) -> None:
    """Truncate planning tables before each test for a deterministic starting state."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(text(f"truncate table {_PLANNING_TABLES} cascade"))
        await session.commit()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An unscoped session (superuser) for inspecting raw stored columns."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def repository(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> DocumentRepository:
    return DocumentRepository(session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher)


@pytest.fixture
async def user_context(db_session: AsyncSession) -> RequestContext:
    user_id = uuid.uuid4()
    await _provision_user(db_session, user_id)
    return _context(user_id)


@pytest.fixture
async def user_a(db_session: AsyncSession) -> RequestContext:
    user_id = uuid.uuid4()
    await _provision_user(db_session, user_id)
    return _context(user_id)


@pytest.fixture
async def user_b(db_session: AsyncSession) -> RequestContext:
    user_id = uuid.uuid4()
    await _provision_user(db_session, user_id)
    return _context(user_id)


@pytest.fixture
def chat_repository(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> ChatRepository:
    return ChatRepository(session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher)


@pytest.fixture
def service(
    repository: DocumentRepository, chat_repository: ChatRepository, cipher: LocalEnvelopeCipher
) -> PlanningService:
    """A planning service wired to the test DB and a deterministic chat service."""
    from app.agent.graph import build_assistant_graph

    graph = build_assistant_graph(DeterministicProvider(delay_seconds=0.0), repository)
    chat = ChatService(chat_repository, DeterministicProvider(), timedelta(days=7), graph=graph)
    return PlanningService(repository=repository, cipher=cipher, chat=chat)


@pytest.fixture
async def pending_approval(service: PlanningService, user_context: RequestContext) -> uuid.UUID:
    """A freshly created pending approval owned by the default test user."""
    draft = await service.create_document_draft(user_context, type="task", title="pending", body="body")
    return draft.approval_id


# --- API fixtures ------------------------------------------------------------


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
    # RabbitMQ background task during unrelated suites. Point the app at the SAME
    # test database the repository fixtures use, so the API layer and the repo layer
    # agree (otherwise API tests hit the dev DB).
    return Settings(
        internal_token=TEST_INTERNAL_TOKEN,
        outbox_poller_enabled=False,
        database_url=DATABASE_URL,
    )


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
