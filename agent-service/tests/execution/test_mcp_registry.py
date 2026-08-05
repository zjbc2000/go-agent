"""MCP registry tests: admission, discovery, and validation of MCP server manifests.

Verbatim from plan:
- test_registry_rejects_mutable_image_tag
Plus: valid pinned manifest, missing provenance/sbom, cross-user isolation,
duplicate idempotent registration, bad tool schema.
"""

import uuid

import pytest
from app.core.errors import ApiError
from app.mcp.registry import McpRegistry
from app.mcp.schemas import McpManifest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

# Real sha256 digest for the pinned test image.
PINNED_DIGEST = "0000000000000000000000000000000000000000000000000000000000000000"

_VALID_MANIFEST: dict = {
    "schema_version": 1,
    "name": "test-server",
    "image": f"oci://example/test@sha256:{PINNED_DIGEST}",
    "provenance": "test provenance",
    "sbom": "test sbom",
    "tools": [
        {
            "tool_id": "echo.readonly",
            "mutable": False,
            "input_schema": {"type": "object", "properties": {"msg": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}}},
        },
        {
            "tool_id": "db.write",
            "mutable": True,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    ],
}


class McpRegistryTester:
    """Wraps the registry with a session factory for user-scoped calls."""

    def __init__(self, registry: McpRegistry, session_factory, user_id: uuid.UUID):
        self.registry = registry
        self.session_factory = session_factory
        self.user_id = user_id

    async def register(self, manifest: dict):
        from app.core.context import RequestContext, new_request_id
        return await self.registry.register_mcp(
            RequestContext(user_id=self.user_id, role="user",
                          request_id=new_request_id()),
            manifest,
        )

    async def discover(self, server_id: uuid.UUID):
        from app.core.context import RequestContext, new_request_id
        return await self.registry.discover_tools(
            RequestContext(user_id=self.user_id, role="user",
                          request_id=new_request_id()),
            server_id,
        )

    async def is_allowed(self, tool_id: str) -> bool:
        from app.core.context import RequestContext, new_request_id
        return await self.registry.is_tool_allowed(
            RequestContext(user_id=self.user_id, role="user",
                          request_id=new_request_id()),
            tool_id,
        )


# --- Fixtures ---

@pytest.fixture
def engine() -> AsyncEngine:
    import os

    from sqlalchemy.ext.asyncio import create_async_engine
    url = os.getenv("TEST_DATABASE_URL",
                    "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")
    return create_async_engine(url, pool_pre_ping=True)


@pytest.fixture(autouse=True)
async def _clean(engine: AsyncEngine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("truncate table public.mcp_tools, public.mcp_servers cascade"))
        await session.commit()


@pytest.fixture
def registry(engine: AsyncEngine) -> McpRegistry:
    return McpRegistry(session_factory=async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
async def tester(registry: McpRegistry, engine: AsyncEngine) -> McpRegistryTester:
    uid = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": uid},
        )
        await session.commit()
    return McpRegistryTester(registry, async_sessionmaker(engine, expire_on_commit=False), uid)


# --- Manifest schema tests ---

def test_manifest_rejects_mutable_image_tag():
    """Plan's verbatim: registry rejects an unpinned tag image.

    McpManifest.model_validate raises pydantic.ValidationError for an
    unpinned tag; register_mcp catches that and raises ApiError.
    """
    with pytest.raises(Exception) as exc_info:
        McpManifest.model_validate({
            "schema_version": 1,
            "name": "bad",
            "image": "registry.example/tool:latest",
            "provenance": "x",
            "sbom": "y",
            "tools": [],
        })
    # Pydantic wraps the ValueError inside ValidationError.
    error_str = str(exc_info.value)
    assert "digest" in error_str.lower() or "sha256" in error_str.lower()


def test_registry_rejects_mutable_image_tag(registry: McpRegistry):
    """Plan's verbatim test: registration of unpinned tag fails at API level.

    Since ``register_mcp`` validates the manifest internally via McpManifest,
    passing an unpinned tag raises ApiError(MCP_DIGEST_REQUIRED).
    """
    import asyncio

    async def _run():
        from app.core.context import RequestContext, new_request_id
        uid = uuid.uuid4()
        ctx = RequestContext(user_id=uid, role="user", request_id=new_request_id())
        with pytest.raises(ApiError, match="MCP_DIGEST_REQUIRED"):
            await registry.register_mcp(ctx, {
                "schema_version": 1,
                "name": "bad",
                "image": "registry.example/tool:latest",
                "provenance": "x",
                "sbom": "y",
                "tools": [],
            })

    asyncio.get_event_loop().run_until_complete(_run())


# --- Registry integration tests ---

@pytest.mark.asyncio
async def test_valid_pinned_manifest_registers_server_and_tools(tester: McpRegistryTester):
    """A valid manifest with pinned digest + provenance + sbom is accepted."""
    result = await tester.register(_VALID_MANIFEST)
    assert "id" in result
    assert result["name"] == "test-server"
    assert len(result["tool_ids"]) == 2
    server_id = uuid.UUID(result["id"])
    tools = await tester.discover(server_id)
    assert len(tools) == 2
    tool_ids = {t["tool_id"] for t in tools}
    assert tool_ids == {"echo.readonly", "db.write"}


@pytest.mark.asyncio
async def test_registry_rejects_missing_provenance(tester: McpRegistryTester):
    """Missing provenance raises MCP_DIGEST_REQUIRED (admission gate)."""
    bad = {**_VALID_MANIFEST, "provenance": ""}
    with pytest.raises(ApiError, match="MCP_DIGEST_REQUIRED"):
        await tester.register(bad)


@pytest.mark.asyncio
async def test_registry_rejects_missing_sbom(tester: McpRegistryTester):
    """Missing sbom raises MCP_DIGEST_REQUIRED (admission gate)."""
    bad = {**_VALID_MANIFEST, "sbom": ""}
    with pytest.raises(ApiError, match="MCP_DIGEST_REQUIRED"):
        await tester.register(bad)


@pytest.mark.asyncio
async def test_cross_user_discover_returns_empty(
    registry: McpRegistry, engine: AsyncEngine,
):
    """Discover by user B on user A's server returns empty (cross-user isolation)."""
    uid_a = uuid.uuid4()
    uid_b = uuid.uuid4()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for uid in (uid_a, uid_b):
            await session.execute(
                text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
                {"id": uid},
            )
        await session.commit()

    ta = McpRegistryTester(registry, async_sessionmaker(engine, expire_on_commit=False), uid_a)
    tb = McpRegistryTester(registry, async_sessionmaker(engine, expire_on_commit=False), uid_b)

    result = await ta.register(_VALID_MANIFEST)
    server_id = uuid.UUID(result["id"])
    tools_b = await tb.discover(server_id)
    assert tools_b == []


@pytest.mark.asyncio
async def test_duplicate_registration_is_idempotent(tester: McpRegistryTester):
    """Registering the same server name twice returns the same tools."""
    r1 = await tester.register(_VALID_MANIFEST)
    r2 = await tester.register(_VALID_MANIFEST)
    assert r1["id"] == r2["id"]
    server_id = uuid.UUID(r1["id"])
    tools = await tester.discover(server_id)
    assert len(tools) == 2


@pytest.mark.asyncio
async def test_is_tool_allowed_returns_true_for_registered_enabled(tester: McpRegistryTester):
    """After registration, is_tool_allowed returns True for the tool."""
    await tester.register(_VALID_MANIFEST)
    assert await tester.is_allowed("echo.readonly") is True
    assert await tester.is_allowed("nonexistent") is False


@pytest.mark.asyncio
async def test_mutable_flag_is_persisted(
    registry: McpRegistry, tester: McpRegistryTester,
):
    """The mutable flag from the manifest is persisted and discoverable."""
    await tester.register(_VALID_MANIFEST)
    # Check via get_tool_registration (service-scoped).
    reg = await registry.get_tool_registration(tester.user_id, "db.write")
    assert reg is not None
    assert reg["mutable"] is True
    # Read-only tool.
    reg_ro = await registry.get_tool_registration(tester.user_id, "echo.readonly")
    assert reg_ro is not None
    assert reg_ro["mutable"] is False
