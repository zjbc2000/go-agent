"""Typed API errors and the FastAPI error-handling boundary."""

from dataclasses import dataclass, replace
from typing import Any, Final, Literal, cast

from fastapi import Request
from fastapi.responses import JSONResponse

ErrorCode = Literal[
    "AUTH_REQUIRED",
    "AUTHZ_DENIED",
    "VALIDATION_FAILED",
    "NOT_FOUND",
    "IDEMPOTENCY_CONFLICT",
    "MODEL_UNAVAILABLE",
    "RATE_LIMITED",
    "STREAM_INTERRUPTED",
    "APPROVAL_CONFLICT",
    "APPROVAL_EXPIRED",
    "MCP_UNAVAILABLE",
    "SANDBOX_DENIED",
    "SANDBOX_TIMEOUT",
    "INTERNAL_ERROR",
]

_STATUS_BY_CODE: Final[dict[ErrorCode, int]] = {
    "AUTH_REQUIRED": 401,
    "AUTHZ_DENIED": 403,
    "VALIDATION_FAILED": 422,
    "NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "MODEL_UNAVAILABLE": 503,
    "RATE_LIMITED": 429,
    "STREAM_INTERRUPTED": 503,
    "APPROVAL_CONFLICT": 409,
    "APPROVAL_EXPIRED": 409,
    "MCP_UNAVAILABLE": 503,
    "SANDBOX_DENIED": 403,
    "SANDBOX_TIMEOUT": 504,
    "INTERNAL_ERROR": 500,
}


@dataclass
class ApiError(Exception):
    """A typed, user-safe API error serialized into the standard envelope.

    Deliberately not frozen: ``asynccontextmanager`` re-attaches ``__traceback__``
    to exceptions propagating out of an ``async with`` block (e.g. ``user_scoped_session``),
    which frozen dataclasses reject with ``FrozenInstanceError``. Instances are still
    treated as immutable in practice; the request-id copy uses ``dataclasses.replace``.
    """

    code: ErrorCode
    message: str
    retryable: bool
    request_id: str | None = None

    def to_body(self) -> dict[str, dict[str, Any]]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "requestId": self.request_id,
                "retryable": self.retryable,
            }
        }


def status_for_code(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 500)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Registered only for ApiError; Starlette's handler signature is broad.
    error = cast(ApiError, exc)
    if error.request_id is None:
        request_id = request.headers.get("x-request-id")
        if request_id is not None:
            error = replace(error, request_id=request_id)
    return JSONResponse(status_code=status_for_code(error.code), content=error.to_body())
