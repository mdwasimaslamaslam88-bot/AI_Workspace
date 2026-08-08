from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.v1 import health
from app.main import app

def test_liveness_is_independent_of_external_services():
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["X-Request-ID"]

def test_legacy_health_endpoint_remains_compatible():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_readiness_reports_unconfigured_dependencies():
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "dependencies": {"postgresql": {"status": "unconfigured"}, "redis": {"status": "unconfigured"}, "ollama": {"status": "unconfigured"}}}

async def _ready(_client):
    return None

async def _unavailable(_client):
    raise RuntimeError("secret-bearing URL must not be returned")

def test_readiness_reports_dependency_status_without_secrets(monkeypatch):
    with TestClient(app) as client:
        app.state.postgres_engine = object()
        app.state.redis_client = object()
        app.state.ollama_client = object()
        monkeypatch.setattr(health, "check_postgres", _ready)
        monkeypatch.setattr(health, "check_redis", _ready)
        monkeypatch.setattr(health, "check_ollama", _unavailable)
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["dependencies"] == {"postgresql": {"status": "ready"}, "redis": {"status": "ready"}, "ollama": {"status": "unavailable"}}
    assert "secret-bearing" not in response.text


def test_readiness_handles_missing_lifespan_state():
    standalone_app = FastAPI()
    standalone_app.include_router(health.router, prefix="/api/v1")
    with TestClient(standalone_app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert all(status == {"status": "unconfigured"} for status in response.json()["dependencies"].values())
