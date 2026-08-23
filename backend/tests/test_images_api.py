from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.images as images_module
from app.api.dependencies import get_current_user
from app.api.v1.images import router
from app.db.dependencies import get_db_session
from app.models import Asset, AssetProvenanceKind, MessageRole, User
from app.services.image import (
    ImageOperationConflictError,
    ImageOperationResult,
    ImageOperationUnavailableError,
)
from app.services.generation_admission import GenerationAdmissionRejectedError


@pytest.fixture
def images_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.asset_storage = object()
    app.state.model_catalog = object()
    app.state.image_generation_runtime = object()
    app.state.image_editing_runtime = object()
    app.state.generation_admission_controller = object()
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock(
        generate_for_owner=AsyncMock(),
        edit_for_owner=AsyncMock(),
    )
    monkeypatch.setattr(images_module, "ImageService", Mock(return_value=service))

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, service, user


def _result(conversation_id, owner_id, *, source_asset_id=None):
    model_id = "comfyui:" + "a" * 24
    asset_id = uuid4()
    kind = (
        AssetProvenanceKind.IMAGE_EDITING
        if source_asset_id is not None
        else AssetProvenanceKind.IMAGE_GENERATION
    )
    asset = Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="local-image.png",
        media_type="image/png",
        byte_size=128,
        content_sha256="b" * 64,
        storage_key=f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}",
        upload_idempotency_key=uuid4(),
        provenance_kind=kind,
        source_asset_id=source_asset_id,
        runtime_id="comfyui",
        model_id=model_id,
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        deleted_at=None,
    )
    attachment = SimpleNamespace(
        asset_id=asset.id,
        position=1,
        state="active",
        original_filename=asset.original_filename,
        media_type=asset.media_type,
        byte_size=asset.byte_size,
        provenance_kind=kind,
        source_asset_id=source_asset_id,
    )
    message = SimpleNamespace(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=(
            "Edited an image locally."
            if source_asset_id is not None
            else "Generated an image locally."
        ),
        sequence_number=2,
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        asset_links=[attachment],
        citation_links=[],
    )
    return ImageOperationResult(asset, message, True)


def test_generation_is_authenticated_idempotent_and_omits_private_prompt(images_api):
    client, service, user = images_api
    conversation_id = uuid4()
    result = _result(conversation_id, user.id)
    service.generate_for_owner.return_value = result
    key = uuid4()
    model_id = "comfyui:" + "a" * 24

    response = client.post(
        "/api/v1/images/generations",
        headers={"Idempotency-Key": str(key)},
        json={
            "conversation_id": str(conversation_id),
            "model_id": model_id,
            "prompt": "private prompt must stay out of response",
            "width": 512,
            "height": 512,
            "steps": 8,
            "guidance": 6,
            "seed": 7,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset"]["provenance_kind"] == "image_generation"
    assert body["message"]["attachments"][0]["id"] == str(result.asset.id)
    assert "private prompt" not in response.text
    assert "storage_key" not in response.text
    service.generate_for_owner.assert_awaited_once_with(
        user.id,
        key,
        conversation_id,
        model_id,
        "private prompt must stay out of response",
        negative_prompt="",
        width=512,
        height=512,
        steps=8,
        guidance=6.0,
        seed=7,
    )


def test_edit_returns_new_identity_and_source_provenance(images_api):
    client, service, user = images_api
    conversation_id = uuid4()
    source_id = uuid4()
    result = _result(conversation_id, user.id, source_asset_id=source_id)
    service.edit_for_owner.return_value = result

    response = client.post(
        "/api/v1/images/edits",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "conversation_id": str(conversation_id),
            "model_id": "comfyui:" + "a" * 24,
            "source_asset_id": str(source_id),
            "instruction": "make a safe local edit",
            "seed": 9,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset"]["id"] != str(source_id)
    assert body["asset"]["source_asset_id"] == str(source_id)
    assert body["message"]["attachments"][0]["source_asset_id"] == str(source_id)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ImageOperationConflictError(), 409),
        (ImageOperationUnavailableError(), 503),
        (GenerationAdmissionRejectedError(), 429),
        (TimeoutError(), 504),
    ],
)
def test_generation_hides_runtime_failures(images_api, error, expected_status):
    client, service, _user = images_api
    service.generate_for_owner.side_effect = error

    response = client.post(
        "/api/v1/images/generations",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "conversation_id": str(uuid4()),
            "model_id": "comfyui:" + "a" * 24,
            "prompt": "private runtime failure detail",
        },
    )

    assert response.status_code == expected_status
    assert "private runtime failure detail" not in response.text


def test_image_requests_reject_unbounded_dimensions_before_service(images_api):
    client, service, _user = images_api

    response = client.post(
        "/api/v1/images/generations",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "conversation_id": str(uuid4()),
            "model_id": "comfyui:" + "a" * 24,
            "prompt": "bounded prompt",
            "width": 1024,
            "height": 1024,
            "steps": 31,
        },
    )

    assert response.status_code == 422
    service.generate_for_owner.assert_not_awaited()
