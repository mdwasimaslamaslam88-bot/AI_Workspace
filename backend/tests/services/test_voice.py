from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelDescriptor,
    ModelModality,
    ResolvedModel,
)
from app.audio import TranscriptionResult
from app.models import Asset, AssetProvenanceKind
from app.services.asset import AssetContent, AssetUploadError, AssetUploadResult
from app.services.generation_admission import (
    GenerationAdmissionController,
    GenerationAdmissionRejectedError,
)
from app.services.voice import (
    VoiceAssetNotFoundError,
    VoiceIdempotencyConflictError,
    VoiceModelUnavailableError,
    VoiceService,
)
from app.storage.local import LocalAssetStorage


def _resolved(runtime_id: str, reference: str, capability: ModelCapability):
    model_id = runtime_id + ":" + "a" * 24
    return ResolvedModel(
        ModelDescriptor(
            model_id=model_id,
            display_name="Local speech model",
            runtime_id=runtime_id,
            modality=ModelModality.AUDIO,
            family="Local speech",
            parameter_class="small",
            capabilities=(capability,),
            context_window=None,
            quantization=None,
            estimated_vram_bytes=None,
            availability=ModelAvailability.AVAILABLE,
            required_vram_bytes=0,
            required_ram_bytes=1,
            installed=True,
            runnable_now=True,
        ),
        reference,
    )


@pytest.mark.asyncio
async def test_transcription_reads_only_owned_bounded_audio_outside_transaction(
    tmp_path, monkeypatch
):
    owner_id = uuid4()
    asset_id = uuid4()
    storage = LocalAssetStorage(tmp_path / "assets")
    writer = storage.begin_write(asset_id)
    writer.write(b"RIFF-private-audio")
    storage_key = writer.finalize()
    resolved = _resolved(
        "faster_whisper",
        "small.en@pinned",
        ModelCapability.SPEECH_RECOGNITION,
    )
    catalog = Mock(resolve_model=AsyncMock(return_value=resolved))
    admission = GenerationAdmissionController(1)

    async def transcribe(_audio: bytes) -> TranscriptionResult:
        with pytest.raises(GenerationAdmissionRejectedError):
            async with admission.admit(uuid4()):
                pass
        return TranscriptionResult("local transcript", "en", 1.0)

    recognition = Mock(
        runtime_id="faster_whisper",
        model_reference="small.en@pinned",
        transcribe=AsyncMock(side_effect=transcribe),
    )
    session = AsyncMock(spec=AsyncSession)
    content = AssetContent(
        storage_key=storage_key,
        original_filename="voice.wav",
        media_type="audio/wav",
        byte_size=len(b"RIFF-private-audio"),
    )
    get_content = AsyncMock(return_value=content)
    monkeypatch.setattr(
        "app.services.voice.AssetService.get_content_for_owner", get_content
    )
    service = VoiceService(
        session,
        storage,
        catalog,
        recognition_runtime=recognition,
        synthesis_runtime=None,
        gpu_admission_controller=admission,
    )

    result = await service.transcribe_for_owner(
        owner_id, asset_id, resolved.descriptor.model_id
    )

    assert result.text == "local transcript"
    recognition.transcribe.assert_awaited_once_with(b"RIFF-private-audio")
    assert session.rollback.await_count >= 1
    get_content.assert_awaited_once_with(owner_id, asset_id)


@pytest.mark.asyncio
async def test_transcription_hides_unowned_asset_before_runtime(tmp_path, monkeypatch):
    recognition = Mock(
        runtime_id="faster_whisper",
        model_reference="small.en@pinned",
        transcribe=AsyncMock(),
    )
    resolved = _resolved(
        recognition.runtime_id,
        recognition.model_reference,
        ModelCapability.SPEECH_RECOGNITION,
    )
    monkeypatch.setattr(
        "app.services.voice.AssetService.get_content_for_owner",
        AsyncMock(return_value=None),
    )
    service = VoiceService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        recognition_runtime=recognition,
        synthesis_runtime=None,
    )

    with pytest.raises(VoiceAssetNotFoundError):
        await service.transcribe_for_owner(
            uuid4(), uuid4(), resolved.descriptor.model_id
        )

    recognition.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesis_persists_new_owned_asset_with_runtime_provenance(
    tmp_path, monkeypatch
):
    owner_id = uuid4()
    resolved = _resolved(
        "piper", "lessac@pinned", ModelCapability.SPEECH_SYNTHESIS
    )
    synthesis = Mock(
        runtime_id="piper",
        model_reference="lessac@pinned",
        synthesize=AsyncMock(return_value=b"RIFF-generated-wave"),
    )
    asset_id = uuid4()
    asset = Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="local-speech.wav",
        media_type="audio/wav",
        byte_size=19,
        content_sha256="b" * 64,
        storage_key=f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}",
        upload_idempotency_key=uuid4(),
        provenance_kind=AssetProvenanceKind.SPEECH_SYNTHESIS,
        runtime_id="piper",
        model_id=resolved.descriptor.model_id,
        created_at=datetime.now(timezone.utc),
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=None),
        create_generated_for_owner=AsyncMock(
            return_value=AssetUploadResult(asset, True)
        ),
    )
    monkeypatch.setattr(
        "app.services.voice.AssetService", Mock(return_value=asset_service)
    )
    service = VoiceService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        recognition_runtime=None,
        synthesis_runtime=synthesis,
    )

    result = await service.synthesize_for_owner(
        owner_id, uuid4(), resolved.descriptor.model_id, "Private local reply"
    )

    assert result.asset is asset and result.created is True
    synthesis.synthesize.assert_awaited_once_with("Private local reply")
    values = asset_service.create_generated_for_owner.await_args.kwargs
    assert values["content"] == b"RIFF-generated-wave"
    assert values["provenance_kind"] is AssetProvenanceKind.SPEECH_SYNTHESIS
    assert values["model_id"] == resolved.descriptor.model_id


@pytest.mark.asyncio
async def test_synthesis_rejects_idempotency_key_from_upload(tmp_path, monkeypatch):
    owner_id = uuid4()
    asset = Mock(
        provenance_kind=AssetProvenanceKind.UPLOAD,
        model_id=None,
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=asset)
    )
    monkeypatch.setattr(
        "app.services.voice.AssetService", Mock(return_value=asset_service)
    )
    synthesis = Mock(
        runtime_id="piper",
        model_reference="voice",
        synthesize=AsyncMock(),
    )
    service = VoiceService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(),
        recognition_runtime=None,
        synthesis_runtime=synthesis,
    )

    with pytest.raises(VoiceIdempotencyConflictError):
        await service.synthesize_for_owner(
            owner_id, uuid4(), "piper:" + "a" * 24, "text"
        )

    synthesis.synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesis_rejects_conflicting_asset_won_during_create_race(
    tmp_path, monkeypatch
):
    owner_id = uuid4()
    resolved = _resolved(
        "piper", "lessac@pinned", ModelCapability.SPEECH_SYNTHESIS
    )
    conflicting = Mock(
        deleted_at=None,
        provenance_kind=AssetProvenanceKind.UPLOAD,
        source_asset_id=None,
        runtime_id=None,
        model_id=None,
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=None),
        create_generated_for_owner=AsyncMock(
            return_value=AssetUploadResult(conflicting, False)
        ),
    )
    monkeypatch.setattr(
        "app.services.voice.AssetService", Mock(return_value=asset_service)
    )
    synthesis = Mock(
        runtime_id="piper",
        model_reference="lessac@pinned",
        synthesize=AsyncMock(return_value=b"RIFF-generated-wave"),
    )
    session = AsyncMock(spec=AsyncSession)
    service = VoiceService(
        session,
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        recognition_runtime=None,
        synthesis_runtime=synthesis,
    )

    with pytest.raises(VoiceIdempotencyConflictError):
        await service.synthesize_for_owner(
            owner_id, uuid4(), resolved.descriptor.model_id, "Private reply"
        )

    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_synthesis_hides_generated_asset_storage_failure(tmp_path, monkeypatch):
    resolved = _resolved(
        "piper", "lessac@pinned", ModelCapability.SPEECH_SYNTHESIS
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=None),
        create_generated_for_owner=AsyncMock(
            side_effect=AssetUploadError("unsafe generated bytes")
        ),
    )
    monkeypatch.setattr(
        "app.services.voice.AssetService", Mock(return_value=asset_service)
    )
    synthesis = Mock(
        runtime_id="piper",
        model_reference="lessac@pinned",
        synthesize=AsyncMock(return_value=b"invalid"),
    )
    service = VoiceService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        recognition_runtime=None,
        synthesis_runtime=synthesis,
    )

    with pytest.raises(VoiceModelUnavailableError):
        await service.synthesize_for_owner(
            uuid4(), uuid4(), resolved.descriptor.model_id, "Private reply"
        )
