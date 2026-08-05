"""FastAPI application factory."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.deps import get_request_context, require_internal_token
from app.chat.provider import build_provider
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.core.config import Settings
from app.core.context import RequestContext
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError, api_error_handler
from app.execution.grant import GrantVerifier
from app.execution.publisher import AioPikaRabbitMqClient, OutboxPublisher
from app.execution.tool_broker import ToolBroker
from app.mcp.registry import McpRegistry
from app.mcp.router import router as mcp_router
from app.mcp.validator import McpValidator
from app.planning.router import router as planning_router
from app.planning.service import PlanningService
from app.repositories.chat import ChatRepository
from app.repositories.execution import ExecutionRepository
from app.repositories.planning import DocumentRepository
from app.skills.router import router as skills_router
from app.skills.service import SkillService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Poll the transactional outbox on an interval while the app runs."""
        if not settings.outbox_poller_enabled:
            yield
            return
        poller = asyncio.create_task(
            _outbox_poll_loop(publisher, settings.outbox_poll_interval_seconds)
        )
        try:
            yield
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=_lifespan)
    app.add_api_route("/healthz", _healthz)
    app.add_exception_handler(ApiError, api_error_handler)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = LocalEnvelopeCipher.from_base64_key(settings.crypto_key_b64)
    repo = ChatRepository(session_factory=session_factory, cipher=cipher)
    service = ChatService(
        repo=repo,
        provider=build_provider(settings),
        retention=timedelta(seconds=settings.stream_event_retention_seconds),
    )
    planning_repository = DocumentRepository(session_factory=session_factory, cipher=cipher)
    planning_service = PlanningService(
        repository=planning_repository,
        cipher=cipher,
        chat=service,
    )
    execution_repository = ExecutionRepository(session_factory=session_factory, cipher=cipher)
    app.state.execution_repository = execution_repository
    mcp_registry = McpRegistry(session_factory=session_factory)
    mcp_validator = McpValidator()
    skill_service = SkillService(
        documents=planning_repository,
        execution=execution_repository,
        cipher=cipher,
        mcp_registry=mcp_registry,
    )
    publisher = OutboxPublisher(
        session_factory=session_factory,
        rabbitmq_client=AioPikaRabbitMqClient(settings.rabbitmq_url, queue=settings.outbox_queue),
        queue=settings.outbox_queue,
        max_attempts=settings.outbox_max_attempts,
        backoff_base_seconds=settings.outbox_backoff_base_seconds,
        backoff_cap_seconds=settings.outbox_backoff_cap_seconds,
    )

    app.state.settings = settings
    app.state.chat_service = service
    app.state.planning_service = planning_service
    app.state.planning_repository = planning_repository
    app.state.skill_service = skill_service
    app.state.outbox_publisher = publisher
    grant_verifier = GrantVerifier(settings.tool_grant_secret)
    tool_broker = ToolBroker(
        session_factory=session_factory,
        documents=planning_repository,
        cipher=cipher,
        grant_verifier=grant_verifier,
        mcp_registry=mcp_registry,
        mcp_executor=None,  # Real executor wired when Docker is available.
        mcp_validator=mcp_validator,
    )
    app.state.tool_broker = tool_broker
    app.state.mcp_registry = mcp_registry
    app.include_router(chat_router)
    app.include_router(planning_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)

    # --- Internal sandbox tool broker route ---

    @app.post("/internal/v1/sandbox/tools/invoke")
    async def sandbox_tool_invoke(request: Request):
        """Broker a tool invocation from an isolated sandbox container.

        Authenticated via the ``X-Tool-Grant`` header (NOT the standard
        internal token + JWT — the sandbox container has only the grant).
        """
        grant_token = request.headers.get("X-Tool-Grant", "")
        if not grant_token:
            raise ApiError("SANDBOX_GRANT_INVALID", "Missing X-Tool-Grant header.", False)
        try:
            body = await request.json()
        except ValueError:
            raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
        if not isinstance(body, dict):
            raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
        step_id = body.get("step_id")
        tool_id = body.get("tool_id")
        input_data = body.get("input")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ApiError("VALIDATION_FAILED", "step_id is required.", False)
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ApiError("VALIDATION_FAILED", "tool_id is required.", False)
        if not isinstance(input_data, dict):
            raise ApiError("VALIDATION_FAILED", "input must be an object.", False)
        result = await tool_broker.invoke(grant_token, step_id, tool_id, input_data)
        return {"data": result.data} if result.success else {
            "error": {"code": result.error_code, "message": result.error_message}
        }

    # --- TEST-ONLY endpoint (Task 5 E2E) ---
    #
    # Backdates the caller's newest pending execution approval so the UI can prove
    # an expired decision never queues a run. Gated by AGENT_TEST_MODE=true:
    # production never sets the flag, so this route raises AUTHZ_DENIED there.

    @app.post("/internal/v1/test/expire-latest-approval")
    async def test_expire_latest_approval(
        request: Request,
        _: None = Depends(require_internal_token),
        context: RequestContext = Depends(get_request_context),
    ) -> dict:
        settings: Settings = request.app.state.settings
        if not settings.test_mode:
            raise ApiError("AUTHZ_DENIED", "Test endpoints are disabled outside AGENT_TEST_MODE.", False)
        repository: ExecutionRepository = request.app.state.execution_repository
        approval = await repository.expire_latest_pending_approval(context)
        if approval is None:
            raise ApiError("NOT_FOUND", "No pending execution approval to expire.", False)
        return {
            "approvalId": str(approval.id),
            "status": approval.status,
            "expiresAt": approval.expires_at.isoformat(),
        }

    return app


async def _outbox_poll_loop(publisher: OutboxPublisher, interval_seconds: int) -> None:
    """Periodically drain the outbox; a transient failure never crashes the app."""
    while True:
        try:
            published = await publisher.publish_pending_outbox_events()
            if published:
                logger.info("Published %d pending outbox event(s)", published)
        except Exception:
            logger.exception("Outbox poll failed")
        await asyncio.sleep(interval_seconds)


def _healthz() -> dict[str, str]:
    return {"service": "agent-service", "status": "ok"}


# Module-level ASGI app for `uvicorn app.main:app`. Tests call create_app() directly.
app = create_app()
