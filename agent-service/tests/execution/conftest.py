"""Fixtures for skill compiler, execution-approval, and API tests.

The repository runs every user-scoped transaction as the ``authenticated`` role
with ``request.jwt.claims`` derived from the ``RequestContext``, so RLS policies
evaluate against the caller. A skill is a planning document of category ``skill``
whose body is the JSON manifest, so skill fixtures provision a ``documents`` +
``document_versions`` row owned by the default test user.
"""

import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.api.deps import decode_request_context
from app.chat.deps import get_request_context
from app.core.config import Settings
from app.core.context import RequestContext, UserRole, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError
from app.main import create_app
from app.repositories.execution import ExecutionRepository
from app.repositories.planning import DocumentRepository
from app.skills.service import SkillService
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

TEST_INTERNAL_TOKEN = "test-internal-token"

# Local Supabase Postgres (see `supabase status`). Override with TEST_DATABASE_URL
# to point at a different database.
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

_EXECUTION_TABLES = (
    "public.mcp_tools, public.mcp_servers, public.outbox_events, public.sandbox_runs, "
    "public.execution_approvals, public.audit_logs, public.approvals, public.document_drafts, "
    "public.document_versions, public.documents"
)

_WRITE_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {"id": "s1", "tool": "document.create", "input": {"type": "task", "title": "{{title}}", "body": "skill body"}}
    ],
}

_READ_MANIFEST = {
    "schema_version": 1,
    "steps": [{"id": "s1", "tool": "document.read", "input": {"document_id": "{{document_id}}"}}],
}


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
async def _clean_execution_tables(engine: AsyncEngine) -> None:
    """Truncate execution and planning tables before each test for a deterministic state."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(text(f"truncate table {_EXECUTION_TABLES} cascade"))
        await session.commit()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An unscoped session (superuser) for inspecting raw stored columns."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def repository(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> DocumentRepository:
    return DocumentRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher
    )


@pytest.fixture
def execution_repository(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> ExecutionRepository:
    return ExecutionRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher
    )


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
def service(
    repository: DocumentRepository, execution_repository: ExecutionRepository, cipher: LocalEnvelopeCipher
) -> SkillService:
    return SkillService(documents=repository, execution=execution_repository, cipher=cipher)


async def _create_skill(repository: DocumentRepository, user: RequestContext, manifest: dict) -> uuid.UUID:
    document = await repository.create_active(
        user, type="skill", title="skill", body=json.dumps(manifest)
    )
    return document.id


@pytest.fixture
async def active_skill(repository: DocumentRepository, user_context: RequestContext) -> uuid.UUID:
    """A write-skill document (document.create step) owned by the default test user."""
    return await _create_skill(repository, user_context, _WRITE_MANIFEST)


@pytest.fixture
async def active_read_skill(repository: DocumentRepository, user_context: RequestContext) -> uuid.UUID:
    """A read-only skill document (document.read step) owned by the default test user."""
    return await _create_skill(repository, user_context, _READ_MANIFEST)


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
    return Settings(internal_token=TEST_INTERNAL_TOKEN)


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
