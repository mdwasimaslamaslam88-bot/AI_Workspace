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
)
from app.images import (
    MAX_IMAGE_INPUT_BYTES,
    ImageEditingRuntime,
    ImageGenerationRuntime,
    ImageRuntimeInputError,
    ImageRuntimeUnavailableError,
    image_dimensions,
)
from app.models.asset import Asset, AssetProvenanceKind
from app.models.message import Message, MessageRole
from app.services.asset import (
    AssetProvenanceUnavailableError,
    AssetService,
    AssetUploadError,
)
from app.services.conversation import ConversationService
from app.services.generation_admission import GenerationAdmissionController
from app.services.message import (
    MessageAppendConflictError,
    MessageAttachmentUnavailableError,
    MessageService,
)
from app.storage.base import AssetStorage
from app.storage.local import StorageError


_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})


class ImageOperationNotFoundError(RuntimeError):
    """An owner-scoped conversation, asset, or public model was unavailable."""


class ImageOperationUnavailableError(RuntimeError):
    """The selected local image runtime cannot safely run now."""


class ImageOperationConflictError(RuntimeError):
    """An idempotency key or attachment was used by another operation."""


@dataclass(frozen=True, slots=True)
class ImageOperationResult:
    asset: Asset
    message: Message
    created: bool


class ImageService:
    def __init__(
        self,
        session: AsyncSession,
        storage: AssetStorage,
        catalog: ModelCatalog,
        *,
        generation_runtime: ImageGenerationRuntime | None,
        editing_runtime: ImageEditingRuntime | None,
        gpu_admission_controller: GenerationAdmissionController | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.catalog = catalog
        self.generation_runtime = generation_runtime
        self.editing_runtime = editing_runtime
        self.gpu_admission_controller = gpu_admission_controller

    async def generate_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
        conversation_id: UUID,
        model_id: str,
        prompt: str,
        *,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        guidance: float,
        seed: int,
    ) -> ImageOperationResult:
        runtime = self.generation_runtime
        if runtime is None:
            raise ImageOperationUnavailableError("image generation is unavailable")
        existing = await AssetService(
            self.session, self.storage
        ).get_by_idempotency_key_for_owner(owner_id, idempotency_key)
        if existing is not None:
            if not self._matches(
                existing,
                AssetProvenanceKind.IMAGE_GENERATION,
                runtime.runtime_id,
                model_id,
                source_asset_id=None,
            ):
                raise ImageOperationConflictError(
                    "idempotency key belongs to another asset operation"
                )
            return await self._attach_result(
                owner_id, conversation_id, existing, created=False
            )

        await self._require_conversation(owner_id, conversation_id)
        await self._resolve_model(
            model_id,
            runtime.runtime_id,
            runtime.model_reference,
            ModelCapability.IMAGE_GENERATION,
        )
        if self.gpu_admission_controller is None:
            output = await runtime.generate(
                prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                steps=steps,
                guidance=guidance,
                seed=seed,
            )
        else:
            async with self.gpu_admission_controller.admit(owner_id):
                output = await runtime.generate(
                    prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    steps=steps,
                    guidance=guidance,
                    seed=seed,
                )
        try:
            stored = await AssetService(
                self.session, self.storage
            ).create_generated_for_owner(
                owner_id,
                idempotency_key,
                filename="local-image.png",
                claimed_media_type="image/png",
                content=output.content,
                provenance_kind=AssetProvenanceKind.IMAGE_GENERATION,
                source_asset_id=None,
                runtime_id=runtime.runtime_id,
                model_id=model_id,
            )
        except (AssetUploadError, OSError, StorageError) as exc:
            raise ImageOperationUnavailableError(
                "image output could not be stored safely"
            ) from exc
        if not self._matches(
            stored.asset,
            AssetProvenanceKind.IMAGE_GENERATION,
            runtime.runtime_id,
            model_id,
            source_asset_id=None,
        ):
            raise ImageOperationConflictError(
                "idempotency key belongs to another asset operation"
            )
        return await self._attach_result(
            owner_id,
            conversation_id,
            stored.asset,
            created=stored.created,
        )

    async def edit_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
        conversation_id: UUID,
        model_id: str,
        source_asset_id: UUID,
        instruction: str,
        *,
        mask_asset_id: UUID | None,
        negative_prompt: str,
        steps: int,
        guidance: float,
        denoise: float,
        seed: int,
    ) -> ImageOperationResult:
        runtime = self.editing_runtime
        if runtime is None:
            raise ImageOperationUnavailableError("image editing is unavailable")
        existing = await AssetService(
            self.session, self.storage
        ).get_by_idempotency_key_for_owner(owner_id, idempotency_key)
        if existing is not None:
            if not self._matches(
                existing,
                AssetProvenanceKind.IMAGE_EDITING,
                runtime.runtime_id,
                model_id,
                source_asset_id=source_asset_id,
            ):
                raise ImageOperationConflictError(
                    "idempotency key belongs to another asset operation"
                )
            return await self._attach_result(
                owner_id, conversation_id, existing, created=False
            )

        await self._require_conversation(owner_id, conversation_id)
        await self._resolve_model(
            model_id,
            runtime.runtime_id,
            runtime.model_reference,
            ModelCapability.IMAGE_EDITING,
        )
        source, source_media_type = await self._owned_image(
            owner_id, source_asset_id
        )
        mask = None
        mask_media_type = None
        if mask_asset_id is not None:
            mask, mask_media_type = await self._owned_image(owner_id, mask_asset_id)
        if self.gpu_admission_controller is None:
            output = await runtime.edit(
                source,
                source_media_type,
                instruction,
                negative_prompt=negative_prompt,
                mask=mask,
                mask_media_type=mask_media_type,
                steps=steps,
                guidance=guidance,
                denoise=denoise,
                seed=seed,
            )
        else:
            async with self.gpu_admission_controller.admit(owner_id):
                output = await runtime.edit(
                    source,
                    source_media_type,
                    instruction,
                    negative_prompt=negative_prompt,
                    mask=mask,
                    mask_media_type=mask_media_type,
                    steps=steps,
                    guidance=guidance,
                    denoise=denoise,
                    seed=seed,
                )
        try:
            stored = await AssetService(
                self.session, self.storage
            ).create_generated_for_owner(
                owner_id,
                idempotency_key,
                filename="local-image-edit.png",
                claimed_media_type="image/png",
                content=output.content,
                provenance_kind=AssetProvenanceKind.IMAGE_EDITING,
                source_asset_id=source_asset_id,
                runtime_id=runtime.runtime_id,
                model_id=model_id,
            )
        except AssetProvenanceUnavailableError as exc:
            raise ImageOperationNotFoundError("image source is unavailable") from exc
        except AssetUploadError as exc:
            raise ImageOperationUnavailableError(
                "image output could not be stored safely"
            ) from exc
        except (OSError, StorageError) as exc:
            raise ImageOperationUnavailableError(
                "image output could not be stored safely"
            ) from exc
        if not self._matches(
            stored.asset,
            AssetProvenanceKind.IMAGE_EDITING,
            runtime.runtime_id,
            model_id,
            source_asset_id=source_asset_id,
        ):
            raise ImageOperationConflictError(
                "idempotency key belongs to another asset operation"
            )
        return await self._attach_result(
            owner_id,
            conversation_id,
            stored.asset,
            created=stored.created,
        )

    async def _owned_image(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> tuple[bytes, str]:
        metadata = await AssetService(
            self.session, self.storage
        ).get_content_for_owner(owner_id, asset_id)
        if metadata is None:
            raise ImageOperationNotFoundError("image source is unavailable")
        if (
            metadata.media_type not in _IMAGE_MEDIA_TYPES
            or not 0 < metadata.byte_size <= MAX_IMAGE_INPUT_BYTES
        ):
            raise ImageRuntimeInputError("image source is unsupported")
        try:
            content = await asyncio.to_thread(
                self._read_exact,
                metadata.storage_key,
                metadata.byte_size,
            )
        except (OSError, StorageError) as exc:
            raise ImageOperationUnavailableError(
                "image source content is unavailable"
            ) from exc
        image_dimensions(content, metadata.media_type)
        return content, metadata.media_type

    async def _require_conversation(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> None:
        conversation = await ConversationService(self.session).get_for_owner(
            owner_id, conversation_id
        )
        await self.session.rollback()
        if conversation is None:
            raise ImageOperationNotFoundError("conversation is unavailable")

    async def _resolve_model(
        self,
        model_id: str,
        runtime_id: str,
        runtime_reference: str,
        capability: ModelCapability,
    ) -> None:
        try:
            resolved = await self.catalog.resolve_model(model_id)
        except (ModelRuntimeUnavailableError, TimeoutError) as exc:
            raise ImageOperationUnavailableError(
                "local image model catalog is unavailable"
            ) from exc
        if resolved is None:
            raise ImageOperationNotFoundError("image model is unavailable")
        descriptor = resolved.descriptor
        if (
            descriptor.runtime_id != runtime_id
            or resolved.runtime_reference != runtime_reference
            or capability not in descriptor.capabilities
        ):
            raise ImageOperationNotFoundError("image model is unavailable")
        if (
            descriptor.availability is not ModelAvailability.AVAILABLE
            or not descriptor.installed
            or not descriptor.runnable_now
        ):
            raise ImageOperationUnavailableError("image model cannot run now")

    async def _attach_result(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        asset: Asset,
        *,
        created: bool,
    ) -> ImageOperationResult:
        expected_content = (
            "Edited an image locally."
            if asset.provenance_kind == AssetProvenanceKind.IMAGE_EDITING
            else "Generated an image locally."
        )
        messages = MessageService(self.session)
        linked = await messages.get_by_attachment_for_owner(owner_id, asset.id)
        if linked is not None:
            if (
                linked.conversation_id != conversation_id
                or linked.role != MessageRole.ASSISTANT
                or linked.content != expected_content
            ):
                raise ImageOperationConflictError(
                    "generated asset belongs to another message operation"
                )
            return ImageOperationResult(asset, linked, False)
        try:
            message = await messages.append_for_owner(
                owner_id,
                conversation_id,
                MessageRole.ASSISTANT,
                expected_content,
                attachment_ids=(asset.id,),
            )
        except (MessageAppendConflictError, MessageAttachmentUnavailableError):
            linked = await messages.get_by_attachment_for_owner(owner_id, asset.id)
            if (
                linked is not None
                and linked.conversation_id == conversation_id
                and linked.role == MessageRole.ASSISTANT
                and linked.content == expected_content
            ):
                return ImageOperationResult(asset, linked, False)
            raise ImageOperationConflictError(
                "generated image could not be attached"
            ) from None
        if message is None:
            raise ImageOperationNotFoundError("conversation is unavailable")
        return ImageOperationResult(asset, message, created)

    @staticmethod
    def _matches(
        asset: Asset,
        provenance_kind: AssetProvenanceKind,
        runtime_id: str,
        model_id: str,
        *,
        source_asset_id: UUID | None,
    ) -> bool:
        return (
            asset.deleted_at is None
            and asset.provenance_kind == provenance_kind
            and asset.runtime_id == runtime_id
            and asset.model_id == model_id
            and asset.source_asset_id == source_asset_id
        )

    def _read_exact(self, storage_key: str, expected: int) -> bytes:
        with self.storage.open_read(storage_key) as handle:
            content = handle.read(MAX_IMAGE_INPUT_BYTES + 1)
        if len(content) != expected or len(content) > MAX_IMAGE_INPUT_BYTES:
            raise StorageError("image object does not match bounded metadata")
        return content
