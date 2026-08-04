"""Typed error envelope and FastAPI handler tests."""

from app.core.errors import ApiError
from app.main import create_app
from fastapi.testclient import TestClient


def test_api_error_has_safe_envelope():
    assert ApiError("VALIDATION_FAILED", "Invalid input", False).to_body()["error"]["retryable"] is False


def test_api_error_handler_returns_safe_envelope():
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise ApiError("VALIDATION_FAILED", "Invalid input", False)

    response = TestClient(app).get("/boom")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["message"] == "Invalid input"
    assert body["error"]["retryable"] is False
    assert "requestId" in body["error"]
