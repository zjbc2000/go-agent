"""FastAPI application factory."""

from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.provider import build_provider
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.core.config import Settings
from app.core.crypto import LocalEnvelopeCipher
from app.core.errors import ApiError, api_error_handler
from app.planning.router import router as planning_router
from app.planning.service import PlanningService
from app.repositories.chat import ChatRepository
from app.repositories.planning import DocumentRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title=settings.app_name, version=settings.version)
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

    app.state.settings = settings
    app.state.chat_service = service
    app.state.planning_service = planning_service
    app.state.planning_repository = planning_repository
    app.include_router(chat_router)
    app.include_router(planning_router)
    return app


def _healthz() -> dict[str, str]:
    return {"service": "agent-service", "status": "ok"}


# Module-level ASGI app for `uvicorn app.main:app`. Tests call create_app() directly.
app = create_app()
