from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.features import FEATURE_REGISTRY
from app.main import app
from app.db.dependencies import get_db_session
from app.models.user import User


def _current_user() -> User:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    return User(id=uuid4(), created_at=now, updated_at=now)


def test_feature_registry_is_complete_and_honest():
    assert len(FEATURE_REGISTRY) >= 140
    assert len({feature.id for feature in FEATURE_REGISTRY}) == len(FEATURE_REGISTRY)
    assert {feature.layer for feature in FEATURE_REGISTRY} == {
        "ai_presence",
        "mission_control",
        "universal_workspace",
        "ai_command_center",
        "apps_hub",
    }
    for feature in FEATURE_REGISTRY:
        assert feature.ui_entry_point.startswith("/")
        assert feature.backend_capability
        assert feature.test_coverage
        if feature.status == "external_dependency":
            assert feature.dependencies
        if feature.status == "planned":
            assert "manual:documented_gap" in feature.test_coverage


def test_feature_registry_requires_authentication():
    session = AsyncMock(spec=AsyncSession)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/features")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 401


def test_authenticated_feature_registry_response_matches_source():
    user = _current_user()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/features")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["product"] == "AI OS"
    assert payload["count"] == len(FEATURE_REGISTRY)
    assert len(payload["items"]) == payload["count"]
    assert all("test_coverage" in item for item in payload["items"])
