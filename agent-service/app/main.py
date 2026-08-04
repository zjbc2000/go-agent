"""FastAPI application factory."""

from fastapi import FastAPI

from app.core.config import Settings
from app.core.errors import ApiError, api_error_handler


def create_app() -> FastAPI:
    settings = Settings.from_env()
    app = FastAPI(title=settings.app_name, version=settings.version)
    app.add_api_route("/healthz", _healthz)
    app.add_exception_handler(ApiError, api_error_handler)
    return app


def _healthz() -> dict[str, str]:
    return {"service": "agent-service", "status": "ok"}
