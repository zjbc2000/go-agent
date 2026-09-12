"""SQLAlchemy async engine, session factory, and the user-scoped transaction boundary.

The service connects to Postgres with a login role that may assume the
``authenticated`` role. Every user-scoped transaction applies the end-user JWT
claims transaction-scoped (``request.jwt.claims`` / ``request.jwt.claim.sub``) so
PostgreSQL RLS policies evaluate against the caller instead of the connection role.
The settings are scoped to the transaction, so pooled connections never leak one
user's identity into another request.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.context import RequestContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def build_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Return an async session factory bound to ``database_url``."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def user_scoped_session(
    factory: async_sessionmaker[AsyncSession], context: RequestContext
) -> AsyncIterator[AsyncSession]:
    """Open a transaction that runs as ``authenticated`` under the caller's JWT claims.

    ``user_id`` always comes from the ``RequestContext`` (derived from the verified
    JWT ``sub``); it is never read from a request body or query parameter.
    """
    async with factory() as session:
        async with session.begin():
            # Postgres does not accept bind parameters in `SET ... = ?`, so set the
            # request-scoped GUCs with set_config(..., is_local => true), which is the
            # same transaction-scoped mechanism PostgREST uses for the JWT claims.
            await session.execute(text("SET LOCAL ROLE authenticated"))
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :claims, true)"),
                {"claims": json.dumps({"sub": str(context.user_id), "role": "authenticated"})},
            )
            await session.execute(
                text("SELECT set_config('request.jwt.claim.sub', :sub, true)"),
                {"sub": str(context.user_id)},
            )
            await session.execute(text("SELECT set_config('request.jwt.claim.role', 'authenticated', true)"))
            yield session


@asynccontextmanager
async def service_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open an unscoped transaction for narrow operational writes (event append).

    The agent service appends stream events on behalf of a running agent; the event
    still records ``user_id`` from the owning run and reads remain RLS-gated.
    """
    async with factory() as session:
        async with session.begin():
            yield session
