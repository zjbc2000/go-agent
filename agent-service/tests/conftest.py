"""Shared pytest fixtures for the agent-service."""

import base64

import pytest
from app.core.crypto import LocalEnvelopeCipher
from app.main import create_app
from fastapi.testclient import TestClient

# Dev/test-only key. Never use the all-zero key outside tests.
TEST_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()


@pytest.fixture
def client() -> TestClient:
    """An in-process test client over the FastAPI application factory."""
    return TestClient(create_app())


@pytest.fixture
def crypto_key() -> str:
    """A base64-encoded Fernet key valid for local tests."""
    return TEST_KEY


@pytest.fixture
def cipher() -> LocalEnvelopeCipher:
    """A LocalEnvelopeCipher backed by the dev/test key."""
    return LocalEnvelopeCipher.from_base64_key(TEST_KEY)
