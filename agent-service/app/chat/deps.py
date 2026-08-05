"""Dependencies for the internal chat router: settings, service, and request identity.

The user-JWT dependency is injectable so tests run without a live Supabase stack
(see ``app/api/deps.py`` for the default verifier/role-loader wiring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Header, Request

from app.api.deps import decode_request_context
from app.core.config import Settings
from app.core.context import RequestContext
from app.core.errors import ApiError

if TYPE_CHECKING:
    from app.chat.service import ChatService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def require_internal_token(
    settings: Settings = Depends(get_settings),
    x_internal_token: str | None = Header(None),
) -> None:
    """Require the shared BFF<->service token. The browser never sees this header."""
    if x_internal_token != settings.internal_token:
        raise ApiError("AUTH_REQUIRED", "Invalid internal token.", False)


def get_request_context(authorization: str | None = Header(None)) -> RequestContext:
    """Decode the end-user JWT forwarded by the BFF into an immutable context."""
    return decode_request_context(authorization)
