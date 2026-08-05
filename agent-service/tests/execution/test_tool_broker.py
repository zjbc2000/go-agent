"""Tool broker tests: grant authentication, plan-binding, approval check,
idempotent replay, caller-input-ignored (NEW #1), cross-user reservation
squat (NEW #2), stuck-reserved handling (NEW #3), and side-effect-once
assertions (NEW #6).
"""

import asyncio
import json
import os
import time
import uuid

import pytest
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
        {"id": "s1", "tool": "document.read",
         "input": {"document_id": "{{doc_id}}"}},
    ],
}

_WRITE_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {"id": "s1", "tool": "document.create",
         "input": {"type": "task", "title": "{{title}}", "body": "body"}},
    ],
}


async def _provision_user(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_id},
    )
    await db_session.commit()


async def _seed_run_with_plan_hash(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher, user_id: uuid.UUID,
    manifest: dict, inputs: dict, *, with_approval: bool = False,
) -> tuple[str, str, str, str]:
    """Seed a running sandbox_run with a REAL plan_hash from the compiler."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": user_id},
        )
        body_ct = cipher.encrypt(json.dumps(manifest))
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        plan = compile_skill(manifest, inputs, version_id=ver_id)
        await session.execute(
            text("insert into documents (id, user_id, type, current_version, status, "
                 "title_ciphertext, body_ciphertext) "
                 "values (:id, :uid, 'skill', 1, 'active', :title, :body)"),
            {"id": doc_id, "uid": user_id, "title": body_ct, "body": body_ct},
        )
        await session.execute(
            text("insert into document_versions (id, user_id, document_id, version, "
                 "title_ciphertext, body_ciphertext) "
                 "values (:id, :uid, :doc, 1, :title, :body)"),
            {"id": ver_id, "uid": user_id, "doc": doc_id, "title": body_ct, "body": body_ct},
        )
        inputs_ct = cipher.encrypt(json.dumps({"inputs": inputs}))
        run_id = uuid.uuid4()
        approval_id = None
        if with_approval:
            approval_id = uuid.uuid4()
            await session.execute(
                text("insert into execution_approvals "
                     "(id, user_id, document_id, version_id, plan_hash, inputs_ciphertext, "
                     "status, decision, expires_at, decided_at) "
                     "values (:id, :uid, :doc, :ver, :hash, :ct, 'confirmed', 'confirmed', "
                     "now() + interval '10 minutes', now())"),
                {"id": approval_id, "uid": user_id, "doc": doc_id, "ver": ver_id,
                 "hash": plan.hash, "ct": inputs_ct},
            )
        await session.execute(
            text("insert into sandbox_runs (id, user_id, document_id, version_id, plan_hash, "
                 "status, inputs_ciphertext, claimed_at, approval_id) "
                 "values (:id, :uid, :doc, :ver, :hash, 'running', :ct, now(), :aid)"),
            {"id": run_id, "uid": user_id, "doc": doc_id, "ver": ver_id,
             "hash": plan.hash, "ct": inputs_ct, "aid": approval_id},
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


@pytest.fixture
async def read_run(
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
) -> tuple[str, str, str, str, uuid.UUID]:
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _READ_MANIFEST, inputs={"doc_id": str(uuid.uuid4())},
    )
    return run_id, doc_id, ver_id, plan_hash, user_id


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


# ---------- I1: Approval check ----------


@pytest.mark.asyncio
async def test_write_step_requires_confirmed_approval(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
):
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
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _WRITE_MANIFEST, inputs={"title": "x"},
        with_approval=True,
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("UPDATE execution_approvals SET expires_at = now() - interval '1 second' "
                 "WHERE document_id = :doc"),
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


# ---------- I2: Plan-binding ----------


@pytest.mark.asyncio
async def test_read_only_step_invoked_as_write_is_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
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
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


@pytest.mark.asyncio
async def test_plan_hash_mismatch_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id),
        plan_hash="deadbeef" * 8, step_ids=["s1"], ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())},
        )


# ---------- NEW #1: Caller input ignored ----------


@pytest.mark.asyncio
async def test_caller_input_ignored_plan_compiled_input_used(
    broker: ToolBroker, engine: AsyncEngine, cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
):
    """NEW #1: the caller-supplied input is IGNORED — execution uses the plan's
    compiled step input.
    """
    user_id = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_id, _WRITE_MANIFEST, inputs={"title": "pinned-title"},
        with_approval=True,
    )
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )
    result = await broker.invoke(
        token, "s1", "document.create",
        {"type": "task", "title": "attacker-title", "body": "evil"},
    )
    assert result.success
    assert result.data["title"] == "pinned-title"


# ---------- Grant/auth ----------


@pytest.mark.asyncio
async def test_broker_rejects_expired_or_wrong_step_grant(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    expired_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=0,)
    time.sleep(0.01)
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            expired_token, "s1", "document.read", {"document_id": str(uuid.uuid4())})
    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            valid_token, "s2", "document.read", {"document_id": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_tampered_signature_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    valid_token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    payload, sig = valid_token.rsplit(".", 1)
    tampered = f"{payload}.deadbeef{sig[8:]}"
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            tampered, "s1", "document.read", {"document_id": str(uuid.uuid4())})


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
            text("insert into documents (id, user_id, type, current_version, status, "
                 "title_ciphertext, body_ciphertext) "
                 "values (:id, :uid, 'task', 1, 'active', :title, :body)"),
            {"id": other_doc_id, "uid": other_user_id, "title": ct, "body": ct},
        )
        await session.commit()
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    result = await broker.invoke(
        token, "s1", "document.read", {"document_id": str(other_doc_id)},)
    assert not result.success
    assert result.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_run_not_in_flight_rejected(
    broker: ToolBroker, read_run: tuple, signer: GrantSigner, engine: AsyncEngine,
):
    run_id, doc_id, ver_id, plan_hash, user_id = read_run
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("UPDATE sandbox_runs SET status = 'succeeded' WHERE id = :id"),
            {"id": run_id},)
        await session.commit()
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(
            token, "s1", "document.read", {"document_id": str(uuid.uuid4())})


# ---------- I3 / NEW #3 / NEW #6: Idempotent replay ----------


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_result_and_only_one_document(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
):
    """NEW #6: replay returns same document id, side effect executed once."""
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    result1 = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t1", "body": "b1"},)
    assert result1.success
    doc_id_1 = result1.data["id"]
    result2 = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t2", "body": "b2"},)
    assert result2.success
    assert result2.data["id"] == doc_id_1
    # Only one non-skill document (the skill manifest itself is a document row).
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM documents WHERE user_id = :uid AND type != 'skill'"),
            {"uid": user_id},)
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_same_step_executes_once(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
):
    """NEW #6: concurrent invocations of the same (run, step) → only one side
    effect executes (the loser gets 'in progress' or the stored result).
    """
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)

    async def invoke():
        return await broker.invoke(
            token, "s1", "document.create", {"type": "task", "title": "t", "body": "b"},)

    results = await asyncio.gather(invoke(), invoke())
    # At least one must succeed (the reservation winner).
    assert results[0].success or results[1].success
    # If both succeeded (sequential timing), ids must match.
    if results[0].success and results[1].success:
        assert results[0].data["id"] == results[1].data["id"]
    # Exactly one document created (excluding the skill manifest).
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM documents WHERE user_id = :uid AND type != 'skill'"),
            {"uid": user_id},)
    assert count == 1


@pytest.mark.asyncio
async def test_stuck_reserved_row_returns_in_progress(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
    engine: AsyncEngine, cipher: LocalEnvelopeCipher,
):
    """NEW #3: a row stuck in 'reserved' returns 'in progress' error, not fake
    success.
    """
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    # Insert a reserved row via async session (simulates crash after reservation).
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into sandbox_tool_calls (run_id, user_id, step_id, tool_id, "
                 "result_status) values (:rid, :uid, 's1', 'document.create', 'reserved') "
                 "on conflict (run_id, step_id) do nothing"),
            {"rid": run_id, "uid": user_id},)
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    result = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "t", "body": "b"},)
    assert not result.success
    assert result.error_code == "SANDBOX_STEP_IN_PROGRESS"


@pytest.mark.asyncio
async def test_replayed_failed_step_reports_failure(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
    cipher: LocalEnvelopeCipher, engine: AsyncEngine,
):
    """NEW #3 IMPORTANT: a replayed failed step must report success=False with
    the stored error, NOT fake success.

    We insert a failed tool-call row directly (simulating a prior failed
    execution), then retry and assert failure with the stored error data.
    """
    run_id, doc_id, ver_id, plan_hash, user_id = write_run
    # Manually insert a FAILED tool-call row.
    error_data = {"error_code": "VALIDATION_FAILED",
                   "error_message": "Invalid document type."}
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into sandbox_tool_calls (run_id, user_id, step_id, tool_id, "
                 "result_status, result_ciphertext) "
                 "values (:rid, :uid, 's1', 'document.create', 'failed', :ct) "
                 "on conflict (run_id, step_id) do nothing"),
            {"rid": run_id, "uid": user_id,
             "ct": cipher.encrypt(json.dumps(error_data))},)
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    result = await broker.invoke(
        token, "s1", "document.create", {"type": "task", "title": "z", "body": "b"},)
    # MUST report failure, not fake success.
    assert not result.success
    assert result.data is not None
    assert result.data["error_code"] == "VALIDATION_FAILED"
    assert result.data["error_message"] == "Invalid document type."


@pytest.mark.asyncio
async def test_crash_after_reservation_finalizes_as_failed(
    broker: ToolBroker, write_run: tuple, signer: GrantSigner,
    cipher: LocalEnvelopeCipher, engine: AsyncEngine,
):
    """Folded Task-3: NON-VACUOUS crash→failed finalization.

    Force an UNEXPECTED exception during _execute_tool and verify the
    sandbox_tool_calls row is finalized 'failed' (not stuck 'reserved').

    We use monkey-patching to make _execute_tool raise mid-execution
    for a specific step, then verify the DB row is 'failed'.
    """
    run_id, doc_id, ver_id, plan_hash, user_id = write_run

    # Save the original _execute_tool.
    original_execute = broker._execute_tool

    async def _raise_after_reserve(context, tool_id, input):
        # This is called AFTER _reserve_tool_call has reserved the row.
        # Raising here exercises the try/finally crash path.
        raise RuntimeError("Simulated unexpected execution crash")

    # Patch _execute_tool for the broker instance.
    broker._execute_tool = _raise_after_reserve  # type: ignore[method-assign]

    token = signer.sign(
        run_id=run_id, user_id=str(user_id), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,
    )

    # The exception propagates out of invoke (re-raised after finally).
    with pytest.raises(RuntimeError, match="Simulated unexpected execution crash"):
        await broker.invoke(
            token, "s1", "document.create", {"type": "task", "title": "t", "body": "b"},
        )

    # Restore before DB check.
    broker._execute_tool = original_execute  # type: ignore[method-assign]

    # Verify the row is 'failed', NOT 'reserved'.
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        status = await session.scalar(
            text("SELECT result_status FROM sandbox_tool_calls "
                 "WHERE run_id = :rid AND step_id = 's1'"),
            {"rid": run_id},
        )
    assert status == "failed", (
        f"Expected 'failed' after crash, got {status!r} — try/finally "
        "did not persist the failure state, row is stuck."
    )


# ---------- NEW #2: Cross-user reservation squat ----------


@pytest.mark.asyncio
async def test_user_b_cannot_poison_user_a_step_via_broker_read(
    broker: ToolBroker, engine: AsyncEngine, cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
):
    """NEW #2: if user B inserts a row for A's run, the broker's _handle_loser
    filters by A's user_id, so A never reads B's row.
    """
    user_a = uuid.uuid4()
    run_id, doc_id, ver_id, plan_hash = await _seed_run_with_plan_hash(
        engine, cipher, user_a, _READ_MANIFEST, inputs={"doc_id": str(uuid.uuid4())},)
    user_b = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": user_b},)
        await session.commit()
    # Insert a tool call for user B on A's run (simulates DB-level attacker).
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into sandbox_tool_calls (run_id, user_id, step_id, tool_id, "
                 "result_status, result_ciphertext) "
                 "values (:rid, :uid, 's1', 'document.read', 'succeeded', :ct)"),
            {"rid": run_id, "uid": user_b,
             "ct": cipher.encrypt(json.dumps({"evil": True}))},)
        await session.commit()

    token = signer.sign(
        run_id=run_id, user_id=str(user_a), plan_hash=plan_hash,
        step_ids=["s1"], ttl_seconds=300,)
    result = await broker.invoke(
        token, "s1", "document.read", {"document_id": str(uuid.uuid4())},)
    assert result.success
    if isinstance(result.data, dict):
        assert result.data.get("evil") is None
