"""Basic application tests."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    """Test the health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check():
    """Test the readiness check endpoint."""
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "checks" in data


def test_required_endpoints_are_documented():
    """Keep the backend API surface visible in OpenAPI."""
    paths = app.openapi()["paths"]
    required_paths = {
        "/metrics",
        "/api/v1/admin/kill-switch",
        "/api/v1/admin/overview",
        "/api/v1/auth/kick/callback",
        "/api/v1/auth/kick/login",
        "/api/v1/auth/logout",
        "/api/v1/billing/checkout",
        "/api/v1/billing/portal",
        "/api/v1/billing/status",
        "/api/v1/channels",
        "/api/v1/channels/amounts",
        "/api/v1/channels/stats",
        "/api/v1/channels/{channel_id}",
        "/api/v1/channels/{channel_id}/predictions",
        "/api/v1/channels/{channel_id}/predictions/{prediction_id}",
        "/api/v1/channels/{channel_id}/stats",
        "/api/v1/me",
        "/api/v1/me/kick-connection",
        "/api/v1/predictions/active",
        "/api/v1/settings",
        "/api/v1/stream",
        "/api/v1/votes",
        "/api/v1/webhooks/dodo",
        "/healthz",
        "/readyz",
    }

    assert required_paths <= paths.keys()
