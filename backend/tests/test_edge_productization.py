from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import app.api.v1.diagnostics as diagnostics_module
from app.ai.catalog import ModelAvailability, ModelCapability, ModelModality
from app.api.dependencies import get_current_user
from app.api.v1.diagnostics import router as diagnostics_router
from app.core.config import Settings
from app.hardware import HardwareInventory
from app.hardware.planner import GIBIBYTE
from app.main import _api_documentation_urls, app
from app.middleware.edge_rate_limit import EdgeRateLimitMiddleware
from app.models.user import User
from app.web import mount_web_application


def _current_user() -> User:
    return User(
        id=uuid4(),
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


def test_application_applies_browser_security_headers_and_hsts_only_for_https():
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers

    with TestClient(app, base_url="https://testserver") as client:
        secure_response = client.get("/api/v1/health/live")
    assert secure_response.headers["Strict-Transport-Security"].startswith(
        "max-age=31536000"
    )


def test_remote_mode_disables_interactive_api_schema():
    remote = FastAPI(**_api_documentation_urls("tailscale"))
    with TestClient(remote) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    assert _api_documentation_urls("local") == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


def test_application_cors_allows_owner_client_headers_but_rejects_arbitrary_ones():
    with TestClient(app) as client:
        accepted = client.options(
            "/api/v1/users/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        rejected = client.options(
            "/api/v1/users/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Arbitrary-Header",
            },
        )
    assert accepted.status_code == 200
    assert accepted.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert rejected.status_code == 400


def test_exact_packaged_desktop_origins_pass_cors_without_a_wildcard():
    configured = Settings(
        _env_file=None,
        BACKEND_CORS_ORIGINS=["tauri://localhost", "http://tauri.localhost"],
    )
    desktop_api = FastAPI()
    desktop_api.add_middleware(
        CORSMiddleware,
        allow_origins=configured.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )

    with TestClient(desktop_api) as client:
        for origin in configured.BACKEND_CORS_ORIGINS:
            response = client.options(
                "/api/v1/users/me",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            assert response.status_code == 200
            assert response.headers["Access-Control-Allow-Origin"] == origin

        rejected = client.options(
            "/api/v1/users/me",
            headers={
                "Origin": "tauri://attacker.invalid",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
    assert rejected.status_code == 400
    assert "Access-Control-Allow-Origin" not in rejected.headers


def test_compiled_web_mount_preserves_api_and_never_serves_source_tree(tmp_path: Path):
    web_root = tmp_path / "dist"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>WORK STATION</h1>", encoding="utf-8")
    (web_root / "app.js").write_text("console.log('public build')", encoding="utf-8")
    standalone = FastAPI()

    @standalone.get("/api/v1/health")
    def health():
        return {"status": "healthy"}

    mount_web_application(standalone, web_root)
    with TestClient(standalone) as client:
        assert client.get("/").text == "<h1>WORK STATION</h1>"
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/v1/health").json() == {"status": "healthy"}
        assert client.get("/../source.py").status_code == 404


def test_compiled_web_mount_fails_closed_without_index(tmp_path: Path):
    with pytest.raises(RuntimeError, match="compiled index.html"):
        mount_web_application(FastAPI(), tmp_path)


def test_edge_limiter_bounds_provisioning_and_auth_failures_without_credentials():
    limited = FastAPI()

    @limited.post("/api/v1/users")
    def provision():
        return {"status": "attempted"}

    @limited.get("/api/v1/private")
    def private(response: Response):
        response.status_code = 401
        return {"status": "denied"}

    limited.add_middleware(
        EdgeRateLimitMiddleware,
        auth_failure_limit=2,
        provisioning_limit=1,
        window_seconds=60,
    )
    with TestClient(limited) as client:
        assert client.post("/api/v1/users").status_code == 200
        provision_limited = client.post("/api/v1/users")
        assert provision_limited.status_code == 429
        assert provision_limited.headers["Cache-Control"] == "no-store"
        assert client.get("/api/v1/private").status_code == 401
        assert client.get("/api/v1/private").status_code == 401
        auth_limited = client.get("/api/v1/private")
    assert auth_limited.status_code == 429
    assert "authorization" not in auth_limited.text.lower()


def test_private_diagnostics_require_auth_and_return_only_bounded_state(monkeypatch):
    standalone = FastAPI()
    standalone.include_router(diagnostics_router, prefix="/api/v1")
    user = _current_user()

    async def current_user_override():
        return user

    model = Mock(
        availability=ModelAvailability.AVAILABLE,
        installed=True,
        runnable_now=True,
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
            ModelCapability.IMAGE_GENERATION,
            ModelCapability.SPEECH_RECOGNITION,
            ModelCapability.SPEECH_SYNTHESIS,
        ),
        modality=ModelModality.TEXT,
    )
    standalone.state.postgres_engine = object()
    standalone.state.redis_client = object()
    standalone.state.ollama_client = object()
    standalone.state.model_catalog = Mock(
        list_models=AsyncMock(return_value=(model,))
    )
    standalone.state.image_generation_runtime = object()
    standalone.state.speech_recognition_runtime = object()
    standalone.state.speech_synthesis_runtime = object()
    standalone.state.asset_storage = object()
    standalone.state.hardware_inventory = HardwareInventory(
        64 * GIBIBYTE,
        (12 * GIBIBYTE,),
        ("NVIDIA Test GPU",),
    )
    monkeypatch.setattr(diagnostics_module, "check_postgres", AsyncMock())
    monkeypatch.setattr(diagnostics_module, "check_redis", AsyncMock())
    monkeypatch.setattr(diagnostics_module, "check_ollama", AsyncMock())

    async def denied_current_user():
        raise HTTPException(status_code=401, detail="Authentication required")

    standalone.dependency_overrides[get_current_user] = denied_current_user
    with TestClient(standalone) as unauthenticated:
        denied = unauthenticated.get("/api/v1/diagnostics")
    assert denied.status_code == 401

    standalone.dependency_overrides[get_current_user] = current_user_override
    try:
        with TestClient(standalone) as client:
            response = client.get("/api/v1/diagnostics")
    finally:
        standalone.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "local"
    assert len(payload["services"]) == 11
    assert payload["gpus"] == [
        {
            "model": "NVIDIA Test GPU",
            "vram_bytes": 12 * GIBIBYTE,
            "hardware_class": "gpu_8_to_15gb",
            "status": "ready",
        }
    ]
    serialized = response.text.lower()
    assert "127.0.0.1" not in serialized
    assert "/home/" not in serialized
    assert "token" not in serialized
