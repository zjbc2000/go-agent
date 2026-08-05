"""Fixtures for planning repository and RLS tests against the local Supabase Postgres.

The repository runs every user-scoped transaction as the ``authenticated`` role
with ``request.jwt.claims`` derived from the ``RequestContext``, so RLS policies
evaluate against the caller. ``documents.user_id`` references ``auth.users(id)``,
so each synthetic test user is provisioned a matching row before the test body runs.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.core.context import RequestContext, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.repositories.planning import DocumentRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# Local Supabase Postgres (see `supabase status`). Override with TEST_DATABASE_URL
# to point at a different database.
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

_PLANNING_TABLES = (
    "public.audit_logs, public.approvals, public.document_drafts, "
    "public.document_versions, public.documents"
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
    return DocumentRepository(
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
