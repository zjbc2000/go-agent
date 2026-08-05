"""Fixtures for chat persistence tests against the local Supabase Postgres.

The repository runs every user-scoped transaction as the ``authenticated`` role
with ``request.jwt.claims`` derived from the ``RequestContext``, so RLS policies
evaluate against the caller. This fixture module only wires up the connection;
see ``app/db/session.py`` for the transaction boundary.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.core.context import RequestContext, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.repositories.chat import ChatRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

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
def user_context() -> RequestContext:
    return _context(uuid.uuid4())


@pytest.fixture
def user_a_context() -> RequestContext:
    return _context(uuid.uuid4())


@pytest.fixture
def user_b_context() -> RequestContext:
    return _context(uuid.uuid4())
