from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.voice as voice_module
from app.api.dependencies import get_current_user
from app.api.v1.voice import router
from app.audio import TranscriptionResult
from app.db.dependencies import get_db_session
from app.models import Asset, AssetProvenanceKind, User
from app.services.voice import (
    VoiceAssetNotFoundError,
    VoiceAssetUnsupportedError,
    VoiceIdempotencyConflictError,
    VoiceModelUnavailableError,
    VoiceSynthesisResult,
)
from app.services.generation_admission import GenerationAdmissionRejectedError


@pytest.fixture
def voice_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.asset_storage = object()
    app.state.model_catalog = object()
    app.state.speech_recognition_runtime = object()
    app.state.speech_synthesis_runtime = object()
    app.state.generation_admission_controller = object()
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock(
        transcribe_for_owner=AsyncMock(),
        synthesize_for_owner=AsyncMock(),
    )
    monkeypatch.setattr(voice_module, "VoiceService", Mock(return_value=service))

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, service, user


def test_transcription_is_authenticated_owner_scoped_and_bounded(voice_api):
    client, service, user = voice_api
    asset_id = uuid4()
    model_id = "faster_whisper:" + "a" * 24
    service.transcribe_for_owner.return_value = TranscriptionResult(
        "local transcript", "en", 1.25
    )

    response = client.post(
        "/api/v1/voice/transcriptions",
        json={"asset_id": str(asset_id), "model_id": model_id},
    )

    assert response.status_code == 201
    assert response.json() == {
        "text": "local transcript",
        "language": "en",
        "duration_seconds": 1.25,
    }
    service.transcribe_for_owner.assert_awaited_once_with(
        user.id, asset_id, model_id
    )


def test_transcription_hides_missing_or_foreign_asset(voice_api):
    client, service, _user = voice_api
    service.transcribe_for_owner.side_effect = VoiceAssetNotFoundError()

    response = client.post(
        "/api/v1/voice/transcriptions",
        json={
            "asset_id": str(uuid4()),
            "model_id": "faster_whisper:" + "a" * 24,
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Voice input or model not found"}


def test_synthesis_returns_only_safe_owned_asset_metadata(voice_api):
    client, service, _user = voice_api
    asset_id = uuid4()
    model_id = "piper:" + "b" * 24
    asset = Asset(
        id=asset_id,
        owner_id=uuid4(),
        original_filename="local-speech.wav",
        media_type="audio/wav",
        byte_size=128,
        content_sha256="c" * 64,
        storage_key=f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}",
        upload_idempotency_key=uuid4(),
        provenance_kind=AssetProvenanceKind.SPEECH_SYNTHESIS,
        source_asset_id=None,
        runtime_id="piper",
        model_id=model_id,
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        deleted_at=None,
    )
    service.synthesize_for_owner.return_value = VoiceSynthesisResult(asset, True)

    response = client.post(
        "/api/v1/voice/syntheses",
        headers={"Idempotency-Key": str(uuid4())},
        json={"model_id": model_id, "text": "Private assistant response"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset"]["id"] == str(asset_id)
    assert body["asset"]["provenance_kind"] == "speech_synthesis"
    assert body["asset"]["model_id"] == model_id
    assert "storage_key" not in response.text
    assert "Private assistant response" not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (VoiceIdempotencyConflictError(), 409),
        (VoiceAssetUnsupportedError(), 422),
        (VoiceModelUnavailableError(), 503),
        (TimeoutError(), 504),
    ],
)
def test_synthesis_translates_runtime_failures_without_private_details(
    voice_api, error, expected_status
):
    client, service, _user = voice_api
    service.synthesize_for_owner.side_effect = error

    response = client.post(
        "/api/v1/voice/syntheses",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "model_id": "piper:" + "a" * 24,
            "text": "Private detail must not return",
        },
    )

    assert response.status_code == expected_status
    assert "Private detail must not return" not in response.text


def test_transcription_returns_bounded_busy_response(voice_api):
    client, service, _user = voice_api
    service.transcribe_for_owner.side_effect = GenerationAdmissionRejectedError()

    response = client.post(
        "/api/v1/voice/transcriptions",
        json={
            "asset_id": str(uuid4()),
            "model_id": "faster_whisper:" + "a" * 24,
        },
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "Local GPU capacity is busy"}
