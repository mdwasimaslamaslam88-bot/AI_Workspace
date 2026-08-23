from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    ModelRuntimeUnavailableError,
    ResolvedModel,
)
from app.audio import (
    SpeechRecognitionRuntime,
    SpeechRuntimeInputError,
    SpeechRuntimeUnavailableError,
    SpeechSynthesisRuntime,
    TranscriptionResult,
)
from app.models.asset import Asset, AssetProvenanceKind
from app.services.asset import AssetService, AssetUploadError, AssetUploadResult
from app.services.generation_admission import GenerationAdmissionController
from app.storage.base import AssetStorage
from app.storage.local import StorageError


ALLOWED_STT_MEDIA_TYPES = frozenset(
    {"audio/wav", "audio/ogg", "audio/mpeg", "audio/webm"}
)


class VoiceModelNotFoundError(RuntimeError):
    """A public model ID did not resolve for the requested speech capability."""


class VoiceModelUnavailableError(RuntimeError):
    """The selected local speech model cannot safely run now."""


class VoiceAssetNotFoundError(RuntimeError):
    """The input audio asset is not active and owned by this user."""


class VoiceAssetUnsupportedError(ValueError):
    """The input asset is not bounded supported audio."""


class VoiceIdempotencyConflictError(RuntimeError):
    """An idempotency key was already used for a different asset operation."""


@dataclass(frozen=True, slots=True)
class VoiceSynthesisResult:
    asset: Asset
    created: bool


class VoiceService:
    def __init__(
        self,
        session: AsyncSession,
        storage: AssetStorage,
        catalog: ModelCatalog,
        *,
        recognition_runtime: SpeechRecognitionRuntime | None,
        synthesis_runtime: SpeechSynthesisRuntime | None,
        gpu_admission_controller: GenerationAdmissionController | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.catalog = catalog
        self.recognition_runtime = recognition_runtime
        self.synthesis_runtime = synthesis_runtime
        self.gpu_admission_controller = gpu_admission_controller

    async def transcribe_for_owner(
        self,
        owner_id: UUID,
        asset_id: UUID,
        model_id: str,
    ) -> TranscriptionResult:
        runtime = self.recognition_runtime
        if runtime is None:
            raise VoiceModelUnavailableError("speech recognition is unavailable")
        await self.session.rollback()
        await self._resolve_model(
            model_id,
            runtime.runtime_id,
            runtime.model_reference,
            ModelCapability.SPEECH_RECOGNITION,
        )
        content = await AssetService(
            self.session, self.storage
        ).get_content_for_owner(owner_id, asset_id)
        if content is None:
            raise VoiceAssetNotFoundError("audio asset not found")
        if content.media_type not in ALLOWED_STT_MEDIA_TYPES:
            raise VoiceAssetUnsupportedError("audio asset type is unsupported")
        from app.runtimes.faster_whisper import MAX_STT_INPUT_BYTES

        if not 0 < content.byte_size <= MAX_STT_INPUT_BYTES:
            raise VoiceAssetUnsupportedError("audio asset size is unsupported")
        try:
            audio = await asyncio.to_thread(
                self._read_exact,
                content.storage_key,
                content.byte_size,
                MAX_STT_INPUT_BYTES,
            )
        except (OSError, StorageError) as exc:
            raise VoiceModelUnavailableError("audio content is unavailable") from exc
        if self.gpu_admission_controller is None:
            return await runtime.transcribe(audio)
        async with self.gpu_admission_controller.admit(owner_id):
            return await runtime.transcribe(audio)

    async def synthesize_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
        model_id: str,
        text: str,
    ) -> VoiceSynthesisResult:
        runtime = self.synthesis_runtime
        if runtime is None:
            raise VoiceModelUnavailableError("speech synthesis is unavailable")
        asset_service = AssetService(self.session, self.storage)
        existing = await asset_service.get_by_idempotency_key_for_owner(
            owner_id, idempotency_key
        )
        if existing is not None:
            if not self._is_matching_synthesis(
                existing,
                runtime_id=runtime.runtime_id,
                model_id=model_id,
            ):
                await self.session.rollback()
                raise VoiceIdempotencyConflictError(
                    "idempotency key belongs to another asset operation"
                )
            return VoiceSynthesisResult(existing, False)
        await self.session.rollback()
        await self._resolve_model(
            model_id,
            runtime.runtime_id,
            runtime.model_reference,
            ModelCapability.SPEECH_SYNTHESIS,
        )
        output = await runtime.synthesize(text)
        try:
            stored: AssetUploadResult = (
                await asset_service.create_generated_for_owner(
                    owner_id,
                    idempotency_key,
                    filename="local-speech.wav",
                    claimed_media_type="audio/wav",
                    content=output,
                    provenance_kind=AssetProvenanceKind.SPEECH_SYNTHESIS,
                    source_asset_id=None,
                    runtime_id=runtime.runtime_id,
                    model_id=model_id,
                )
            )
        except (AssetUploadError, OSError, StorageError) as exc:
            raise VoiceModelUnavailableError(
                "speech output could not be stored safely"
            ) from exc
        if not self._is_matching_synthesis(
            stored.asset,
            runtime_id=runtime.runtime_id,
            model_id=model_id,
        ):
            await self.session.rollback()
            raise VoiceIdempotencyConflictError(
                "idempotency key belongs to another asset operation"
            )
        return VoiceSynthesisResult(stored.asset, stored.created)

    @staticmethod
    def _is_matching_synthesis(
        asset: Asset,
        *,
        runtime_id: str,
        model_id: str,
    ) -> bool:
        return (
            asset.deleted_at is None
            and asset.provenance_kind == AssetProvenanceKind.SPEECH_SYNTHESIS
            and asset.source_asset_id is None
            and asset.runtime_id == runtime_id
            and asset.model_id == model_id
        )

    async def _resolve_model(
        self,
        model_id: str,
        runtime_id: str,
        runtime_reference: str,
        capability: ModelCapability,
    ) -> ResolvedModel:
        try:
            resolved = await self.catalog.resolve_model(model_id)
        except (ModelRuntimeUnavailableError, TimeoutError) as exc:
            raise VoiceModelUnavailableError("local model catalog unavailable") from exc
        if resolved is None:
            raise VoiceModelNotFoundError("speech model not found")
        descriptor = resolved.descriptor
        if (
            descriptor.runtime_id != runtime_id
            or resolved.runtime_reference != runtime_reference
            or capability not in descriptor.capabilities
        ):
            raise VoiceModelNotFoundError("speech model not found")
        if (
            descriptor.availability is not ModelAvailability.AVAILABLE
            or not descriptor.installed
            or not descriptor.runnable_now
        ):
            raise VoiceModelUnavailableError("speech model cannot run now")
        return resolved

    def _read_exact(self, storage_key: str, expected: int, maximum: int) -> bytes:
        with self.storage.open_read(storage_key) as handle:
            value = handle.read(maximum + 1)
        if len(value) != expected or len(value) > maximum:
            raise StorageError("audio object does not match bounded metadata")
        return value
