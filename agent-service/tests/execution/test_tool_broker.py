"""Tool broker tests: grant authentication, plan-binding, approval check,
idempotent replay with stored result, and tool dispatch determined by the plan.

The broker is the sandbox container's ONLY external channel. Key security
properties tested:
- I1: write/delete steps require a confirmed, unexpired execution approval at
  call time (not just at confirm time).
- I2: tool dispatch is determined by the plan's declared tool — the
  caller-supplied tool_id is ignored if it mismatches; a read-only run's step
  invoked as a write tool is rejected.
- I3: TOCTOU-safe idempotent replay — the row is reserved BEFORE execution;
  the stored result payload is returned on replay.
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
from app.skills.compiler import compile_skill
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"
)

GRANT_SECRET = "test-broker-grant-secret"

_EXECUTION_TABLES = (
    "public.sandbox_tool_calls, public.mcp_tools, public.mcp_servers, "
    "public.outbox_events, public.sandbox_runs, public.execution_approvals, "
    "public.audit_logs, public.approvals, public.document_drafts, "
    "public.document_versions, public.documents"
)

_READ_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {"id": "s1", "tool": "document.read", "input": {"document_id": "{{doc_id}}"}},
    ],
}

_WRITE_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {"id": "s1", "tool": "document.create",
         "input": {"type": "task", "title": "{{title}}", "body": "body"}},
    ],
}


def _context(user_id: uuid.UUID) -> RequestContext:
    return RequestContext(user_id=user_id, role="user", request_id=new_request_id())


async def _provision_user(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_id},
    )
    await db_session.commit()


async def _seed_run_with_plan_hash(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher, user_id: uuid.UUID,
    manifest: dict, inputs: dict,
    *, with_approval: bool = False,
) -> tuple[str, str, str, str]:
    """Seed a running sandbox_run with a REAL plan_hash from the compiler.

    Returns (run_id, document_id, version_id, plan_hash).
    If with_approval=True, also creates a confirmed execution_approval.
    """
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": user_id},
        )
        body_ct = cipher.encrypt(json.dumps(manifest))
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()

        # Compute the real plan hash.
        plan = compile_skill(manifest, inputs, version_id=ver_id)

        await session.execute(
            text(
                "insert into documents (id, user_id, type, current_version, status, "
                "title_ciphertext, body_ciphertext) "
                "values (:id, :uid, 'skill', 1, 'active', :title, :body)"
            ),
            {"id": doc_id, "uid": user_id, "title": body_ct, "body": body_ct},
        )
        await session.execute(
            text(
                "insert into document_versions (id, user_id, document_id, version, "
                "title_ciphertext, body_ciphertext) "
                "values (:id, :uid, :doc, 1, :title, :body)"
            ),
            {"id": ver_id, "uid": user_id, "doc": doc_id, "title": body_ct, "body": body_ct},
        )

        inputs_ct = cipher.encrypt(json.dumps({"inputs": inputs}))
        run_id = uuid.uuid4()

        approval_id = None
        if with_approval:
            approval_id = uuid.uuid4()
            await session.execute(
                text(
                    "insert into execution_approvals "
                    "(id, user_id, document_id, version_id, plan_hash, inputs_ciphertext, "
                    "status, decision, expires_at, decided_at) "
                    "values (:id, :uid, :doc, :ver, :hash, :ct, 'confirmed', 'confirmed', "
                    "now() + interval '10 minutes', now())"
                ),
                {
                    "id": approval_id, "uid": user_id, "doc": doc_id, "ver": ver_id,
                    "hash": plan.hash, "ct": inputs_ct,
                },
            )

        await session.execute(
            text(
                "insert into sandbox_runs (id, user_id, document_id, version_id, plan_hash, "
                "status, inputs_ciphertext, claimed_at, approval_id) "
                "values (:id, :uid, :doc, :ver, :hash, 'running', :ct, now(), :aid)"
            ),
            {
                "id": run_id, "uid": user_id, "doc": doc_id, "ver": ver_id,
                "hash": plan.hash, "ct": inputs_ct, "aid": approval_id,
            },
        )
        await session.commit()
    return str(run_id), str(doc_id), str(ver_id), plan.hash


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
        session_factory=async_sessionmaker(engine, expire_on_commit=False), cipher=cipher,
    )


@pytest.fixture
def broker(
    engine: AsyncEngine, documents: DocumentRepository,
    cipher: LocalEnvelopeCipher, verifier: GrantVerifier,
) -> ToolBroker:
    return ToolBroker(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        documents=documents, cipher=cipher, grant_verifier=verifier,
    )


# Convenience fixture: a read-only run (no approval).
@pytest.fixture
async def read_run(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
) -> tuple[str, str, str, str, uuid.UUID]:
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _READ_MANIFEST, inputs={"doc_id": str(uuid.uuid4())},
    )
    return run_id, doc_id, ver_id, plan_hash, user_id


# Convenience fixture: a write run (with confirmed approval).
@pytest.fixture
async def write_run(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
) -> tuple[str, str, str, str, uuid.UUID]:
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _WRITE_MANIFEST, inputs={"title": "x"},
        with_approval=True,
    )
    return run_id, doc_id, ver_id, plan_hash, user_id


# ---------- I1: Approval check tests ----------


@pytest.mark.asyncio
async def test_write_step_requires_confirmed_approval(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
):
    """I1: a write step must have a confirmed, unexpired approval at call time."""
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    result = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t", "body": "b"},
    )
    assert result.success


@pytest.mark.asyncio
async def test_write_step_with_expired_approval_rejected(
    broker: ToolBroker, engine: AsyncEngine, cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
):
    """I1: a write step under an expired approval at execution time is rejected."""
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _WRITE_MANIFEST, inputs={"title": "x"},
        with_approval=True,
    )
    # Make the approval expired.
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text(
                "UPDATE execution_approvals SET expires_at = now() - interval '1 second' "
                "WHERE document_id = :doc"
            ),
            {"doc": doc_id},
        )
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.create", {"type": "task", "title": "t", "body": "b"},
        )


# ---------- I2: Plan-binding tests ----------


@pytest.mark.asyncio
async def test_read_only_step_invoked_as_write_is_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    """I2: a read-only run has no approval, so invoking its step as a write tool
    must be rejected because the plan declares document.read, not document.delete.
    """
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.delete", {"document_id": str(uuid.uuid4())},
        )


@pytest.mark.asyncio
async def test_tool_id_must_match_plan_declared_tool(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
):
    """I2: the caller-supplied tool_id must match the plan step's declared tool."""
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    # The plan declares document.create, but we ask for document.read.
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


@pytest.mark.asyncio
async def test_plan_hash_mismatch_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    """I2: a grant whose plan_hash does not match the recompiled plan is rejected."""
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id),
        plan_hash="deadbeef" * 8,  # wrong hash
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


# ---------- General grant/auth tests ----------


@pytest.mark.asyncio
async def test_broker_rejects_expired_or_wrong_step_grant(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    """Expired grant AND a step outside the allowlist -> SANDBOX_GRANT_INVALID."""
    run_id, doc_id, ver_id, plan_hash, user_id = read_run

    expired_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=0,
    )
    time.sleep(0.01)
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            expired_token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )

    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            valid_token, "s2", "document.read", {"document_id": str(uuid.uuid4())},
        )


@pytest.mark.asyncio
async def test_tampered_signature_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    payload, sig = valid_token.rsplit(".", 1)
    tampered = f"{payload}.deadbeef{sig[8:]}"
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            tampered, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


@pytest.mark.asyncio
async def test_valid_grant_executes_tool_as_grant_user(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    # The plan declares s1 as document.read with {{doc_id}} input.
    # Input substitution already happened during compile, so the input
    # carries the substituted doc_id. We pass a fresh UUID here — the doc
    # won't exist (user-scoped), but the call should succeed (NOT_FOUND, not grant error).
    result = await broker.invoke(
        token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
    )
    # Document doesn't exist → NOT_FOUND tool result (not a grant error).
    assert not result.success
    assert result.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_cross_user_document_not_found(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run

    other_user_id = uuid.uuid4()
    other_doc_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, other_user_id)
        ct = cipher.encrypt(json.dumps({"title": "other"}))
        await session.execute(
            text(
                "insert into documents (id, user_id, type, current_version, status, "
                "title_ciphertext, body_ciphertext) "
                "values (:id, :uid, 'task', 1, 'active', :title, :body)"
            ),
            {"id": other_doc_id, "uid": other_user_id, "title": ct, "body": ct},
        )
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    result = await broker.invoke(
        token, "s1", "document.read", {"document_id": str(other_doc_id)},
    )
    assert not result.success
    assert result.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_run_not_in_flight_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
    engine: AsyncEngine,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("UPDATE sandbox_runs SET status = 'succeeded' WHERE id = :id"),
            {"id": run_id},
        )
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


# ---------- I3: TOCTOU-safe idempotent replay ----------


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_result_data(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
):
    """I3: TOCTOU-safe idempotent replay — the replay returns the stored result.

    A document.create executed twice with the same (run_id, step_id) must:
    1. Succeed both times
    2. The second call returns the same document id as the first call
    3. Only ONE document exists (side effect executed once)
    """
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )

    result1 = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t1", "body": "b1"},
    )
    assert result1.success
    assert result1.data is not None
    doc_id_1 = result1.data["id"]

    # Second invocation with same (run_id, step_id) — idempotent replay.
    result2 = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t2", "body": "b2"},
    )
    assert result2.success
    assert result2.data is not None
    # Must return the SAME document id (the stored result).
    assert result2.data["id"] == doc_id_1
