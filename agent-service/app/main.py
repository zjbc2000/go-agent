"""FastAPI application factory."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.provider import build_provider
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.core.config import Settings
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError, api_error_handler
from app.execution.publisher import AioPikaRabbitMqClient, OutboxPublisher
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
    skill_service = SkillService(
        documents=planning_repository,
        execution=execution_repository,
        cipher=cipher,
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
    app.include_router(chat_router)
    app.include_router(planning_router)
    app.include_router(skills_router)
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
