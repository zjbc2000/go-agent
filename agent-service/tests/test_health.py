"""Health endpoint tests."""


def test_health_returns_service_name(client):
    assert client.get("/healthz").json() == {"service": "agent-service", "status": "ok"}
