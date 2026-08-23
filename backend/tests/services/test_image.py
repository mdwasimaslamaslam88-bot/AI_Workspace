from datetime import datetime, timezone
import struct
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import zlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelDescriptor,
    ModelModality,
    ResolvedModel,
)
from app.images import GeneratedImage
from app.models import Asset, AssetProvenanceKind, Message, MessageRole
from app.services.asset import AssetUploadResult
from app.services.generation_admission import (
    GenerationAdmissionController,
    GenerationAdmissionRejectedError,
)
from app.services.image import (
    ImageOperationConflictError,
    ImageOperationNotFoundError,
    ImageService,
)
from app.storage.local import LocalAssetStorage


def _png(width: int = 512, height: int = 512) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(b"bounded"))
        + chunk(b"IEND", b"")
    )


def _resolved(capability: ModelCapability) -> ResolvedModel:
    return ResolvedModel(
        ModelDescriptor(
            model_id="comfyui:" + "a" * 24,
            display_name="Pinned SDXL",
            runtime_id="comfyui",
            modality=ModelModality.IMAGE,
            family="SDXL",
            parameter_class="3.5B",
            capabilities=(capability,),
            context_window=None,
            quantization="FP16",
            estimated_vram_bytes=9 * 1024**3,
            availability=ModelAvailability.AVAILABLE,
            required_vram_bytes=9 * 1024**3,
            required_ram_bytes=16 * 1024**3,
            installed=True,
            runnable_now=True,
        ),
        "sdxl@pinned",
    )


def _asset(owner_id, model_id, *, source_asset_id=None):
    asset_id = uuid4()
    return Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="local-image.png",
        media_type="image/png",
        byte_size=len(_png()),
        content_sha256="b" * 64,
        storage_key=f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}",
        upload_idempotency_key=uuid4(),
        provenance_kind=(
            AssetProvenanceKind.IMAGE_EDITING
            if source_asset_id is not None
            else AssetProvenanceKind.IMAGE_GENERATION
        ),
        source_asset_id=source_asset_id,
        runtime_id="comfyui",
        model_id=model_id,
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_generation_persists_only_png_provenance_then_attaches_message(
    tmp_path, monkeypatch
):
    owner_id = uuid4()
    conversation_id = uuid4()
    resolved = _resolved(ModelCapability.IMAGE_GENERATION)
    asset = _asset(owner_id, resolved.descriptor.model_id)
    message = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="Generated an image locally.",
        sequence_number=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=None),
        create_generated_for_owner=AsyncMock(
            return_value=AssetUploadResult(asset, True)
        ),
    )
    conversation_service = Mock(get_for_owner=AsyncMock(return_value=object()))
    message_service = Mock(
        get_by_attachment_for_owner=AsyncMock(return_value=None),
        append_for_owner=AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.image.AssetService", Mock(return_value=asset_service)
    )
    monkeypatch.setattr(
        "app.services.image.ConversationService",
        Mock(return_value=conversation_service),
    )
    monkeypatch.setattr(
        "app.services.image.MessageService", Mock(return_value=message_service)
    )
    admission = GenerationAdmissionController(1)

    async def generate(*_args, **_kwargs) -> GeneratedImage:
        with pytest.raises(GenerationAdmissionRejectedError):
            async with admission.admit(uuid4()):
                pass
        return GeneratedImage(_png(), 512, 512)

    runtime = Mock(
        runtime_id="comfyui",
        model_reference="sdxl@pinned",
        generate=AsyncMock(side_effect=generate),
    )
    session = AsyncMock(spec=AsyncSession)
    service = ImageService(
        session,
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        generation_runtime=runtime,
        editing_runtime=None,
        gpu_admission_controller=admission,
    )

    result = await service.generate_for_owner(
        owner_id,
        uuid4(),
        conversation_id,
        resolved.descriptor.model_id,
        "private prompt that must not be persisted",
        negative_prompt="",
        width=512,
        height=512,
        steps=8,
        guidance=6.0,
        seed=7,
    )

    assert result.asset is asset and result.message is message and result.created
    runtime.generate.assert_awaited_once()
    stored = asset_service.create_generated_for_owner.await_args.kwargs
    assert stored["content"] == _png()
    assert stored["provenance_kind"] == AssetProvenanceKind.IMAGE_GENERATION
    assert "private prompt" not in message.content
    message_service.append_for_owner.assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "Generated an image locally.",
        attachment_ids=(asset.id,),
    )
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_generation_idempotency_repairs_unattached_matching_asset(
    tmp_path, monkeypatch
):
    owner_id = uuid4()
    conversation_id = uuid4()
    resolved = _resolved(ModelCapability.IMAGE_GENERATION)
    asset = _asset(owner_id, resolved.descriptor.model_id)
    message = Mock(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="Generated an image locally.",
    )
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=asset)
    )
    message_service = Mock(
        get_by_attachment_for_owner=AsyncMock(return_value=None),
        append_for_owner=AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.image.AssetService", Mock(return_value=asset_service)
    )
    monkeypatch.setattr(
        "app.services.image.MessageService", Mock(return_value=message_service)
    )
    runtime = Mock(
        runtime_id="comfyui",
        model_reference="sdxl@pinned",
        generate=AsyncMock(),
    )
    service = ImageService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(),
        generation_runtime=runtime,
        editing_runtime=None,
    )

    result = await service.generate_for_owner(
        owner_id,
        uuid4(),
        conversation_id,
        resolved.descriptor.model_id,
        "ignored on idempotent replay",
        negative_prompt="",
        width=512,
        height=512,
        steps=8,
        guidance=6.0,
        seed=7,
    )

    assert result.created is False and result.message is message
    runtime.generate.assert_not_awaited()
    message_service.append_for_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_hides_foreign_source_before_runtime(tmp_path, monkeypatch):
    resolved = _resolved(ModelCapability.IMAGE_EDITING)
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(return_value=None),
        get_content_for_owner=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.image.AssetService", Mock(return_value=asset_service)
    )
    monkeypatch.setattr(
        "app.services.image.ConversationService",
        Mock(return_value=Mock(get_for_owner=AsyncMock(return_value=object()))),
    )
    runtime = Mock(
        runtime_id="comfyui",
        model_reference="sdxl@pinned",
        edit=AsyncMock(),
    )
    session = AsyncMock(spec=AsyncSession)
    service = ImageService(
        session,
        LocalAssetStorage(tmp_path / "assets"),
        Mock(resolve_model=AsyncMock(return_value=resolved)),
        generation_runtime=None,
        editing_runtime=runtime,
    )

    with pytest.raises(ImageOperationNotFoundError):
        await service.edit_for_owner(
            uuid4(),
            uuid4(),
            uuid4(),
            resolved.descriptor.model_id,
            uuid4(),
            "edit privately",
            mask_asset_id=None,
            negative_prompt="",
            steps=8,
            guidance=6.0,
            denoise=0.5,
            seed=7,
        )

    runtime.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_rejects_idempotency_key_from_other_provenance(
    tmp_path, monkeypatch
):
    asset_service = Mock(
        get_by_idempotency_key_for_owner=AsyncMock(
            return_value=Mock(
                deleted_at=None,
                provenance_kind=AssetProvenanceKind.UPLOAD,
                source_asset_id=None,
                runtime_id=None,
                model_id=None,
            )
        )
    )
    monkeypatch.setattr(
        "app.services.image.AssetService", Mock(return_value=asset_service)
    )
    runtime = Mock(
        runtime_id="comfyui", model_reference="sdxl@pinned", generate=AsyncMock()
    )
    service = ImageService(
        AsyncMock(spec=AsyncSession),
        LocalAssetStorage(tmp_path / "assets"),
        Mock(),
        generation_runtime=runtime,
        editing_runtime=None,
    )

    with pytest.raises(ImageOperationConflictError):
        await service.generate_for_owner(
            uuid4(),
            uuid4(),
            uuid4(),
            "comfyui:" + "a" * 24,
            "prompt",
            negative_prompt="",
            width=512,
            height=512,
            steps=8,
            guidance=6.0,
            seed=7,
        )

    runtime.generate.assert_not_awaited()
