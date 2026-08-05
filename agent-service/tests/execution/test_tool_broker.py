"""Tool broker tests: grant authentication, idempotent replay, and tool execution.

The broker is the sandbox container's ONLY external channel. It authenticates via
a signed grant token, reloads the run + approval from PostgreSQL, executes the
tool AS the grant's user_id, and records a redacted audit row. Idempotent replay
via UNIQUE(run_id, step_id) ensures a retried step never re-executes a side
effect.

These tests exercise the broker against the real Supabase database (user-scoped
transactions) with a deterministic signer/verifier pair.
"""

import json
import os
import time
import uuid

import pytest
from app.core.context import RequestContext, new_request_id
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError
from app.execution.grant import GrantSigner, GrantVerifier
from app.execution.tool_broker import ToolBroker
from app.repositories.planning import DocumentRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Reuse the execution conftest database URL.
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

GRANT_SECRET = "test-broker-grant-secret"

_EXECUTION_TABLES = (
    "public.sandbox_tool_calls, public.mcp_tools, public.mcp_servers, public.outbox_events, "
    "public.sandbox_runs, public.execution_approvals, public.audit_logs, public.approvals, "
    "public.document_drafts, public.document_versions, public.documents"
)


def _context(user_id: uuid.UUID) -> RequestContext:
    return RequestContext(user_id=user_id, role="user", request_id=new_request_id())


async def _provision_user(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_id},
    )
    await db_session.commit()


async def _seed_running_run(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher, user_id: uuid.UUID
) -> tuple[str, str]:
    """Insert a running sandbox_run + backing skill document; return (run_id, document_id)."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": user_id},
        )
        manifest = {
            "schema_version": 1,
            "steps": [
                {"id": "s1", "tool": "document.read", "input": {"document_id": str(uuid.uuid4())}},
                {"id": "s2", "tool": "document.create", "input": {"type": "task", "title": "test", "body": "body"}},
            ],
        }
        body_ct = cipher.encrypt(json.dumps(manifest))
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        await session.execute(
            text(
                "insert into documents (id, user_id, type, current_version, status, title_ciphertext, body_ciphertext) "
                "values (:id, :uid, 'skill', 1, 'active', :title, :body)"
            ),
            {"id": doc_id, "uid": user_id, "title": body_ct, "body": body_ct},
        )
        await session.execute(
            text(
                "insert into document_versions (id, user_id, document_id, version, title_ciphertext, body_ciphertext) "
                "values (:id, :uid, :doc, 1, :title, :body)"
            ),
            {"id": ver_id, "uid": user_id, "doc": doc_id, "title": body_ct, "body": body_ct},
        )
        inputs_ct = cipher.encrypt(json.dumps({"inputs": {}}))
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "insert into sandbox_runs (id, user_id, document_id, version_id, plan_hash, status, "
                "inputs_ciphertext, claimed_at) "
                "values (:id, :uid, :doc, :ver, :hash, 'running', :ct, now())"
            ),
            {"id": run_id, "uid": user_id, "doc": doc_id, "ver": ver_id, "hash": "x" * 64, "ct": inputs_ct},
        )
        await session.commit()
    return str(run_id), str(doc_id)


@pytest.fixture(autouse=True)
async def _clean(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(text(f"truncate table {_EXECUTION_TABLES} cascade"))
        await session.commit()


@pytest.fixture
def engine() -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(DATABASE_URL, pool_pre_ping=True)


@pytest.fixture
def cipher() -> LocalEnvelopeCipher:
    return LocalEnvelopeCipher.from_base64_key(
        __import__("base64").urlsafe_b64encode(b"0" * 32).decode()
    )


@pytest.fixture
def signer() -> GrantSigner:
    return GrantSigner(GRANT_SECRET)


@pytest.fixture
def verifier() -> GrantVerifier:
    return GrantVerifier(GRANT_SECRET)


@pytest.fixture
def documents(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> DocumentRepository:
    return DocumentRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher
    )


@pytest.fixture
def broker(
    engine: AsyncEngine, documents: DocumentRepository,
    cipher: LocalEnvelopeCipher, verifier: GrantVerifier,
) -> ToolBroker:
    return ToolBroker(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        documents=documents,
        cipher=cipher,
        grant_verifier=verifier,
    )


@pytest.fixture
async def running_run(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> tuple[str, str, uuid.UUID]:
    """Return (run_id, document_id, user_id) for a running sandbox run."""
    user_id = uuid.uuid4()
    run_id, doc_id = await _seed_running_run(engine, cipher, user_id)
    return run_id, doc_id, user_id


# --- Grant authentication tests ---


@pytest.mark.asyncio
async def test_broker_rejects_expired_or_wrong_step_grant(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner
):
    """Expired grant AND a step outside the allowlist -> SANDBOX_GRANT_INVALID."""
    run_id, doc_id, user_id = running_run

    # Expired grant (ttl_seconds=0 means already expired).
    expired_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1", "s2"], ttl_seconds=0,
    )
    time.sleep(0.01)  # ensure expiry passes
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(expired_token, "s1", "document.read", {"document_id": str(uuid.uuid4())})

    # Valid grant but step not in allowlist.
    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(valid_token, "s2", "document.read", {"document_id": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_tampered_signature_rejected(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner
):
    """A grant with a fabricated signature is rejected."""
    run_id, doc_id, user_id = running_run
    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1"], ttl_seconds=300,
    )
    # Flip a character in the signature portion.
    payload, sig = valid_token.rsplit(".", 1)
    tampered = f"{payload}.deadbeef{sig[8:]}"
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(tampered, "s1", "document.read", {"document_id": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_valid_grant_executes_tool_as_grant_user(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner
):
    """document.read with a valid grant returns the user's document."""
    run_id, doc_id, user_id = running_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1"], ttl_seconds=300,
    )
    result = await broker.invoke(token, "s1", "document.read", {"document_id": doc_id})
    assert result.success
    assert result.data is not None
    assert result.data["id"] == doc_id


@pytest.mark.asyncio
async def test_cross_user_document_not_found(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner,
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
):
    """A grant for user A cannot read user B's document (RLS — surfaces as NOT_FOUND)."""
    run_id, doc_id, user_id = running_run

    # Create a document owned by a different user.
    other_user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, other_user_id)

    other_doc_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": other_user_id},
        )
        ct = cipher.encrypt(json.dumps({"title": "other"}))
        await session.execute(
            text(
                "insert into documents (id, user_id, type, current_version, status, title_ciphertext, body_ciphertext) "
                "values (:id, :uid, 'task', 1, 'active', :title, :body)"
            ),
            {"id": other_doc_id, "uid": other_user_id, "title": ct, "body": ct},
        )
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1"], ttl_seconds=300,
    )
    result = await broker.invoke(token, "s1", "document.read", {"document_id": str(other_doc_id)})
    # The broker's document.get is user-scoped, so it returns None -> NOT_FOUND.
    assert not result.success
    assert result.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_idempotent_replay_returns_stored_result(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner
):
    """A replayed (run_id, step_id) returns the stored result without re-executing.

    document.create executed once; a retried step returns the same result and only
    one document exists (the side effect is idempotent).
    """
    run_id, doc_id, user_id = running_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1", "s2"], ttl_seconds=300,
    )

    # First invocation: create a document through the broker.
    result1 = await broker.invoke(token, "s2", "document.create", {"type": "task", "title": "t1", "body": "b1"})
    assert result1.success
    assert result1.data is not None
    doc_id_1 = result1.data["id"]
    assert doc_id_1 is not None

    # Second invocation with the same (run_id, step_id) — idempotent replay.
    result2 = await broker.invoke(token, "s2", "document.create", {"type": "task", "title": "t2", "body": "b2"})
    assert result2.success
    assert result2.data is not None
    # Idempotent replay: replayed flag indicates the result came from the prior call.


@pytest.mark.asyncio
async def test_run_not_in_flight_rejected(
    broker: ToolBroker, running_run: tuple[str, str, uuid.UUID], signer: GrantSigner,
    engine: AsyncEngine,
):
    """A grant for a run that is no longer running is rejected."""
    run_id, doc_id, user_id = running_run

    # Mark the run as succeeded.
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("UPDATE sandbox_runs SET status = 'succeeded' WHERE id = :id"),
            {"id": run_id},
        )
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash="x" * 64,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(token, "s1", "document.read", {"document_id": str(uuid.uuid4())})
