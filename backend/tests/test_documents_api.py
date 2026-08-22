import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.documents as documents_module
from app.api.dependencies import get_current_user
from app.api.v1.documents import router
from app.db.dependencies import get_db_session
from app.models.document import DocumentStatus
from app.models.user import User
from app.services.document import (
    DocumentNotFoundError,
    DocumentRecord,
    DocumentRetrievalUnavailableError,
    RetrievedDocumentChunk,
)


@pytest.fixture
def document_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.asset_storage = object()
    app.state.document_ingestion_admission = asyncio.Semaphore(2)
    app.state.document_ingestion_tasks = {}
    app.state.document_ingestion_max_duration_seconds = 30.0
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.get_for_owner = AsyncMock(return_value=None)
    service.ingest_for_owner = AsyncMock()
    service.cancel_for_owner = AsyncMock(return_value=True)
    service.search_for_owner = AsyncMock(return_value=())
    factory = Mock(return_value=service)
    monkeypatch.setattr(documents_module, "DocumentService", factory)

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, user, service, factory


def _record(asset_id):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return DocumentRecord(
        id=uuid4(),
        asset_id=asset_id,
        status=DocumentStatus.READY,
        source_state="active",
        original_filename="notes.txt",
        media_type="text/plain",
        chunk_count=2,
        character_count=120,
        failure_code=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def test_ingest_returns_safe_status_without_storage_metadata(document_api):
    client, user, service, factory = document_api
    asset_id = uuid4()
    record = _record(asset_id)
    service.ingest_for_owner.return_value = record

    response = client.post(f"/api/v1/documents/assets/{asset_id}/ingest")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["asset_id"] == str(asset_id)
    assert "storage_key" not in response.text
    service.ingest_for_owner.assert_awaited_once_with(user.id, asset_id)
    factory.assert_called_with(
        factory.call_args.args[0],
        factory.call_args.args[1],
        factory.call_args.args[2],
        max_duration_seconds=30.0,
        active_tasks={},
    )


def test_unowned_asset_uses_generic_not_found(document_api):
    client, _user, service, _factory = document_api
    service.ingest_for_owner.side_effect = DocumentNotFoundError()

    response = client.post(f"/api/v1/documents/assets/{uuid4()}/ingest")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document asset not found"}


def test_document_routes_inject_configured_embedding_runtime(document_api):
    client, _user, service, factory = document_api
    runtime = object()
    client.app.state.document_embedding_runtime = runtime

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    factory.assert_called_once_with(
        factory.call_args.args[0],
        factory.call_args.args[1],
        factory.call_args.args[2],
        embedding_runtime=runtime,
        max_duration_seconds=30.0,
        active_tasks={},
    )
    service.list_for_owner.assert_awaited_once()


def test_owner_scoped_search_returns_bounded_provenance(document_api):
    client, user, service, _factory = document_api
    item = RetrievedDocumentChunk(
        chunk_id=uuid4(),
        asset_id=uuid4(),
        content="owned source excerpt",
        score=0.75,
        original_filename="owned.csv",
        provenance_kind="row",
        page_number=None,
        row_start=2,
        row_end=3,
        section=None,
    )
    service.search_for_owner.return_value = (item,)

    response = client.get("/api/v1/documents/search?query=owned&limit=1")

    assert response.status_code == 200
    assert response.json()["items"][0]["content"] == "owned source excerpt"
    assert response.json()["items"][0]["row_start"] == 2
    service.search_for_owner.assert_awaited_once_with(user.id, "owned", limit=1)


def test_embedding_runtime_failure_returns_generic_search_unavailable(document_api):
    client, _user, service, _factory = document_api
    service.search_for_owner.side_effect = DocumentRetrievalUnavailableError()

    response = client.get("/api/v1/documents/search?query=owned")

    assert response.status_code == 503
    assert response.json() == {"detail": "Document search is unavailable"}
    assert "runtime" not in response.text.lower()
