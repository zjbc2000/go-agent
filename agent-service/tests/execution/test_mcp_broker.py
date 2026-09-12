"""MCP broker dispatch tests: enabled tools, disabled tools, mutable approval
gate (NEW #7), output-schema validation, cross-user server denial.

These tests extend the existing ToolBroker test setup with the MCP registry
and a fake executor.
"""

import json
import os
import uuid

import pytest
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError
from app.execution.grant import GrantSigner, GrantVerifier
from app.execution.tool_broker import ToolBroker
from app.mcp.executor import FakeMcpExecutor
from app.mcp.executor import ToolResult as McpToolResult
from app.mcp.registry import McpRegistry
from app.mcp.validator import McpValidator
from app.repositories.planning import DocumentRepository
from app.skills.compiler import compile_skill
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")

GRANT_SECRET = "test-mcp-broker-secret"

_EXECUTION_TABLES = (
    "public.sandbox_tool_calls, public.mcp_tools, public.mcp_servers, "
    "public.outbox_events, public.sandbox_runs, public.execution_approvals, "
    "public.audit_logs, public.approvals, public.document_drafts, "
    "public.document_versions, public.documents"
)

PINNED_DIGEST = "0000000000000000000000000000000000000000000000000000000000000000"

_MCP_READ_MANIFEST = {
    "schema_version": 1,
    "allowed_tools": ["echo.readonly"],
    "steps": [
        {"id": "mcp1", "tool": "echo.readonly", "input": {"message": "{{msg}}"}},
    ],
}

_MCP_WRITE_MANIFEST = {
    "schema_version": 1,
    "allowed_tools": ["db.write"],
    "steps": [
        {"id": "mcp2", "tool": "db.write", "input": {"key": "{{key}}", "value": "{{value}}"}},
    ],
}


async def _seed_mcp_manifest(cipher: LocalEnvelopeCipher, engine: AsyncEngine, user_id: uuid.UUID) -> None:
    """Register an MCP server with tools in the DB (user-scoped)."""
    from app.models.execution import McpServer as McpServerRecord
    from app.models.execution import McpTool as McpToolRecord

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        # Use service-role style insert for test seeding.
        sid = uuid.uuid4()
        session.add(
            McpServerRecord(
                id=sid,
                user_id=user_id,
                name="test-mcp",
                image=f"oci://example/test@sha256:{PINNED_DIGEST}",
                enabled=True,
                provenance="test",
                sbom="test",
            )
        )
        session.add(
            McpToolRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                server_id=sid,
                tool_id="echo.readonly",
                enabled=True,
                mutable=False,
                input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"echo": {"type": "string"}}},
            )
        )
        session.add(
            McpToolRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                server_id=sid,
                tool_id="db.write",
                enabled=True,
                mutable=True,
                input_schema={"type": "object"},
                output_schema={"type": "object", "properties": {"written": {"type": "boolean"}}},
            )
        )
        await session.commit()


async def _provision_user(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_id},
    )
    await db_session.commit()


async def _seed_run_with_plan_hash(
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    user_id: uuid.UUID,
    manifest: dict,
    inputs: dict,
    *,
    with_approval: bool = False,
) -> tuple[str, str, str, str, str]:
    """Seed a running sandbox_run for an MCP skill."""
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
                {"id": approval_id, "uid": user_id, "doc": doc_id, "ver": ver_id, "hash": plan.hash, "ct": inputs_ct},
            )
        await session.execute(
            text(
                "insert into sandbox_runs (id, user_id, document_id, version_id, plan_hash, "
                "status, inputs_ciphertext, claimed_at, approval_id) "
                "values (:id, :uid, :doc, :ver, :hash, 'running', :ct, now(), :aid)"
            ),
            {
                "id": run_id,
                "uid": user_id,
                "doc": doc_id,
                "ver": ver_id,
                "hash": plan.hash,
                "ct": inputs_ct,
                "aid": approval_id,
            },
        )
        await session.commit()
    return str(run_id), str(doc_id), str(ver_id), plan.hash, str(user_id)


# ---- Fixtures ----


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
    import base64

    return LocalEnvelopeCipher.from_base64_key(base64.urlsafe_b64encode(b"0" * 32).decode())


@pytest.fixture
def signer() -> GrantSigner:
    return GrantSigner(GRANT_SECRET)


@pytest.fixture
def verifier() -> GrantVerifier:
    return GrantVerifier(GRANT_SECRET)


@pytest.fixture
def registry(engine: AsyncEngine) -> McpRegistry:
    return McpRegistry(session_factory=async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
def fake_executor() -> FakeMcpExecutor:
    executor = FakeMcpExecutor()
    executor.set_result(
        "echo.readonly",
        McpToolResult(
            success=True,
            data={"echo": "read-only-response"},
        ),
    )
    executor.set_result(
        "db.write",
        McpToolResult(
            success=True,
            data={"written": True},
        ),
    )
    return executor


@pytest.fixture
def validator() -> McpValidator:
    return McpValidator()


@pytest.fixture
def documents(engine: AsyncEngine, cipher: LocalEnvelopeCipher) -> DocumentRepository:
    return DocumentRepository(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        cipher=cipher,
    )


@pytest.fixture
def broker(
    engine: AsyncEngine,
    documents: DocumentRepository,
    cipher: LocalEnvelopeCipher,
    verifier: GrantVerifier,
    registry: McpRegistry,
    fake_executor: FakeMcpExecutor,
    validator: McpValidator,
) -> ToolBroker:
    return ToolBroker(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        documents=documents,
        cipher=cipher,
        grant_verifier=verifier,
        mcp_registry=registry,
        mcp_executor=fake_executor,
        mcp_validator=validator,
    )


# ---- Tests ----


@pytest.mark.asyncio
async def test_mcp_read_tool_executes_via_fake_executor(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
):
    """An enabled MCP read tool executes via the fake executor."""
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_mcp_manifest(cipher, engine, user_id)
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_READ_MANIFEST,
        inputs={"msg": "hello"},
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["mcp1"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "mcp1", "echo.readonly", {"message": "hello"})
    assert result.success
    assert result.data == {"echo": "read-only-response"}


@pytest.mark.asyncio
async def test_disabled_mcp_tool_rejected(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
):
    """A tool that is not registered/enabled → MCP_TOOL_DISABLED."""
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    # Register only "echo.readonly", NOT "mystery.tool".
    await _seed_mcp_manifest(cipher, engine, user_id)
    # Seed a run with a manifest that references a non-registered tool.
    manifest_with_mystery = {
        "schema_version": 1,
        "allowed_tools": ["mystery.tool"],
        "steps": [
            {"id": "bad", "tool": "mystery.tool", "input": {}},
        ],
    }
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        manifest_with_mystery,
        inputs={},
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["bad"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "bad", "mystery.tool", {})
    assert not result.success
    assert result.error_code == "MCP_TOOL_DISABLED"


@pytest.mark.asyncio
async def test_mutable_mcp_tool_requires_approval(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
):
    """A mutable MCP tool step requires an approval (NEW #7).

    Without an approval, the broker rejects.
    """
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_mcp_manifest(cipher, engine, user_id)
    # Seed a run WITHOUT an approval for the mutable tool.
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_WRITE_MANIFEST,
        inputs={"key": "k", "value": "v"},
        with_approval=False,
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["mcp2"],
        ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(token, "mcp2", "db.write", {"key": "k", "value": "v"})


@pytest.mark.asyncio
async def test_mutable_mcp_tool_with_approval_succeeds(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
):
    """A mutable MCP tool WITH an approval executes successfully (NEW #7)."""
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_mcp_manifest(cipher, engine, user_id)
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_WRITE_MANIFEST,
        inputs={"key": "k", "value": "v"},
        with_approval=True,
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["mcp2"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "mcp2", "db.write", {"key": "k", "value": "v"})
    assert result.success
    assert result.data == {"written": True}


@pytest.mark.asyncio
async def test_output_schema_validation_failure_returns_typed_error(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
    fake_executor: FakeMcpExecutor,
):
    """Output that does not match the registered schema → typed failure."""
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_mcp_manifest(cipher, engine, user_id)
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_READ_MANIFEST,
        inputs={"msg": "hi"},
    )
    # Override the executor to return data that does NOT match the output schema.
    # The registered output_schema expects echo to be a string; returning an
    # integer for echo should fail JSON Schema validation.
    fake_executor.set_result(
        "echo.readonly",
        McpToolResult(
            success=True,
            data={"echo": 42},
        ),
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["mcp1"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "mcp1", "echo.readonly", {"message": "hi"})
    assert not result.success
    assert result.error_code == "VALIDATION_FAILED"
    assert "schema" in result.error_message.lower()


@pytest.mark.asyncio
async def test_cross_user_mcp_server_denied(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
):
    """A user B cannot invoke user A's MCP tools."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for uid in (user_a, user_b):
            await _provision_user(session, uid)
    # Register server for user A.
    await _seed_mcp_manifest(cipher, engine, user_a)
    # Seed a run owned by user B.
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_b,
        _MCP_READ_MANIFEST,
        inputs={"msg": "hi"},
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_b),
        plan_hash=plan_hash,
        step_ids=["mcp1"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "mcp1", "echo.readonly", {"message": "hi"})
    # User B has no registered MCP tool "echo.readonly" → disabled.
    assert not result.success
    assert result.error_code == "MCP_TOOL_DISABLED"


# --- IMPORTANT #1: fail-closed mutable default gate ---

_MCP_NO_MUTABLE_FIELD_MANIFEST = {
    "schema_version": 1,
    "allowed_tools": ["power.format"],
    "steps": [
        {"id": "pw1", "tool": "power.format", "input": {"path": "{{path}}"}},
    ],
}


async def _seed_power_tool(cipher: LocalEnvelopeCipher, engine: AsyncEngine, user_id: uuid.UUID) -> None:
    """Register a tool WITHOUT an explicit mutable field (defaults to True)."""
    from app.models.execution import McpServer as McpServerRecord
    from app.models.execution import McpTool as McpToolRecord

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        sid = uuid.uuid4()
        session.add(
            McpServerRecord(
                id=sid,
                user_id=user_id,
                name="power-server",
                image=f"oci://example/power@sha256:{PINNED_DIGEST}",
                enabled=True,
                provenance="test",
                sbom="test",
            )
        )
        session.add(
            McpToolRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                server_id=sid,
                tool_id="power.format",
                enabled=True,
                mutable=True,
                # NOTE: this is the fail-closed default — the DB stores 'mutable'
                # as True because the manifest didn't declare mutable: false.
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                output_schema={"type": "object"},
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_power_tool_without_approval_rejected(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
    fake_executor: FakeMcpExecutor,
):
    """IMPORTANT #1: a tool registered WITHOUT ``mutable: false`` defaults to
    mutable=True → requires an approval. Without one, the broker rejects.
    """
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_power_tool(cipher, engine, user_id)
    fake_executor.set_result(
        "power.format",
        McpToolResult(
            success=True,
            data={"formatted": True},
        ),
    )
    # Seed a run WITHOUT approval.
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_NO_MUTABLE_FIELD_MANIFEST,
        inputs={"path": "/etc/hosts"},
        with_approval=False,
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["pw1"],
        ttl_seconds=300,
    )
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(token, "pw1", "power.format", {"path": "/etc/hosts"})


@pytest.mark.asyncio
async def test_power_tool_with_approval_succeeds(
    broker: ToolBroker,
    engine: AsyncEngine,
    cipher: LocalEnvelopeCipher,
    signer: GrantSigner,
    registry: McpRegistry,
    fake_executor: FakeMcpExecutor,
):
    """IMPORTANT #1: with an approval, a tool that defaulted to mutable succeeds."""
    user_id = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await _provision_user(session, user_id)
    await _seed_power_tool(cipher, engine, user_id)
    fake_executor.set_result(
        "power.format",
        McpToolResult(
            success=True,
            data={"formatted": True},
        ),
    )
    run_id, doc_id, ver_id, plan_hash, _ = await _seed_run_with_plan_hash(
        engine,
        cipher,
        user_id,
        _MCP_NO_MUTABLE_FIELD_MANIFEST,
        inputs={"path": "/tmp"},
        with_approval=True,
    )
    token = signer.sign(
        run_id=run_id,
        user_id=str(user_id),
        plan_hash=plan_hash,
        step_ids=["pw1"],
        ttl_seconds=300,
    )
    result = await broker.invoke(token, "pw1", "power.format", {"path": "/tmp"})
    assert result.success
    assert result.data == {"formatted": True}
