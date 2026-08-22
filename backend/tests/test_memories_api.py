from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.memories as memories_module
from app.api.dependencies import get_current_user
from app.api.v1.memories import router
from app.db.dependencies import get_db_session
from app.models.memory import MemoryCategory
from app.models.user import User
from app.services.memory import MemoryRecord, MemorySettingRecord, RetrievedMemory


@pytest.fixture
def memory_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.create_for_owner = AsyncMock()
    service.forget_for_owner = AsyncMock(return_value=None)
    service.setting_for_owner = AsyncMock(
        return_value=MemorySettingRecord(True, None, None)
    )
    service.set_enabled_for_owner = AsyncMock()
    service.retrieve_for_owner = AsyncMock(return_value=())
    monkeypatch.setattr(memories_module, "MemoryService", Mock(return_value=service))

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, user, service


def _record(*, deleted=False):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return MemoryRecord(
        id=uuid4(),
        category=MemoryCategory.PREFERENCE,
        content=None if deleted else "Use concise answers.",
        provenance_kind="explicit_user_entry",
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
    )


def test_create_is_explicit_and_returns_no_owner_or_embedding(memory_api):
    client, user, service = memory_api
    record = _record()
    service.create_for_owner.return_value = record

    response = client.post(
        "/api/v1/memories",
        json={"category": "preference", "content": "Use concise answers."},
    )

    assert response.status_code == 201
    assert response.json()["provenance_kind"] == "explicit_user_entry"
    assert response.json()["state"] == "active"
    assert "owner_id" not in response.text
    assert "embedding" not in response.text
    service.create_for_owner.assert_awaited_once_with(
        user.id,
        MemoryCategory.PREFERENCE,
        "Use concise answers.",
    )


def test_list_can_inspect_content_free_deleted_tombstone(memory_api):
    client, user, service = memory_api
    service.list_for_owner.return_value = (_record(deleted=True),)

    response = client.get("/api/v1/memories?include_deleted=true")

    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "deleted"
    assert response.json()["items"][0]["content"] is None
    service.list_for_owner.assert_awaited_once_with(
        user.id,
        include_deleted=True,
    )


def test_foreign_memory_uses_generic_not_found(memory_api):
    client, _user, service = memory_api
    service.forget_for_owner.return_value = None

    response = client.delete(f"/api/v1/memories/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Memory not found"}


def test_enable_disable_is_owner_scoped(memory_api):
    client, user, service = memory_api
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    service.set_enabled_for_owner.return_value = MemorySettingRecord(
        False,
        now,
        now,
    )

    response = client.put("/api/v1/memories/settings", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    service.set_enabled_for_owner.assert_awaited_once_with(user.id, False)


def test_search_returns_only_service_selected_owner_memory(memory_api):
    client, user, service = memory_api
    item = RetrievedMemory(
        id=uuid4(),
        category=MemoryCategory.PROJECT_CONTEXT,
        content="Apollo ships Friday.",
        score=0.7,
        created_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    service.retrieve_for_owner.return_value = (item,)

    response = client.get("/api/v1/memories/search?query=Apollo&limit=1")

    assert response.status_code == 200
    assert response.json()["items"][0]["content"] == "Apollo ships Friday."
    service.retrieve_for_owner.assert_awaited_once_with(user.id, "Apollo", limit=1)
