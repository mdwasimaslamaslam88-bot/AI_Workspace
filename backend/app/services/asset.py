from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.asset import Asset
from app.models.asset import AssetProvenanceKind
from app.repositories.asset import AssetRepository
from app.storage.base import AssetStorage, ReconciliationReport, StagedAssetWrite
from app.storage.local import StorageError


logger = get_logger(__name__)
ASSET_COPY_CHUNK_BYTES = 65_536
ASSET_SNIFF_BYTES = 512
_MEDIA_TYPE_PATTERN = re.compile(
    r"\A[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*\Z"
)
_DANGEROUS_MEDIA_TYPES = frozenset(
    {
        "image/svg+xml",
        "text/html",
        "application/xhtml+xml",
        "application/javascript",
        "text/javascript",
        "application/x-msdownload",
        "application/x-executable",
    }
)


class AssetUploadStream(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class AssetUploadError(ValueError):
    """The submitted file cannot become an opaque asset."""


class AssetEmptyError(AssetUploadError):
    """Empty files are not persisted as assets."""


class AssetFilenameInvalidError(AssetUploadError):
    """A submitted display filename contains unsafe control data."""


class AssetProvenanceUnavailableError(AssetUploadError):
    """Generated media provenance cannot be claimed for this owner."""


@dataclass(frozen=True, slots=True)
class AssetUploadResult:
    asset: Asset
    created: bool


@dataclass(frozen=True, slots=True)
class AssetContent:
    storage_key: str
    original_filename: str | None
    media_type: str
    byte_size: int


def _validate_generated_provenance(
    provenance_kind: AssetProvenanceKind,
    source_asset_id: UUID | None,
    runtime_id: str,
    model_id: str,
) -> None:
    if provenance_kind is AssetProvenanceKind.UPLOAD:
        raise ValueError("generated asset provenance cannot be upload")
    if provenance_kind is AssetProvenanceKind.IMAGE_EDITING:
        if source_asset_id is None:
            raise ValueError("image editing provenance requires a source asset")
    elif source_asset_id is not None:
        raise ValueError("this generated asset provenance cannot have a source")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", runtime_id):
        raise ValueError("generated asset runtime ID is invalid")
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}", model_id
    ):
        raise ValueError("generated asset model ID is invalid")


def normalize_original_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    normalized = unicodedata.normalize("NFC", filename)
    normalized = normalized.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not normalized:
        return None
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise AssetFilenameInvalidError("asset filename is invalid")
    return normalized


def _looks_like_mp3(prefix: bytes) -> bool:
    if prefix.startswith(b"ID3"):
        return True
    if len(prefix) < 4 or prefix[0] != 0xFF or prefix[1] & 0xE0 != 0xE0:
        return False
    version = (prefix[1] >> 3) & 0x03
    layer = (prefix[1] >> 1) & 0x03
    bitrate_index = (prefix[2] >> 4) & 0x0F
    sample_rate_index = (prefix[2] >> 2) & 0x03
    return (
        version != 0x01
        and layer != 0x00
        and bitrate_index not in {0x00, 0x0F}
        and sample_rate_index != 0x03
    )


def canonical_media_type(claimed: str | None, prefix: bytes) -> str:
    normalized_claim = (claimed or "").split(";", 1)[0].strip().lower()
    if not _MEDIA_TYPE_PATTERN.fullmatch(normalized_claim):
        normalized_claim = "application/octet-stream"

    lowered = prefix.lstrip().lower()
    if (
        normalized_claim in _DANGEROUS_MEDIA_TYPES
        or prefix.startswith(b"\x7fELF")
        or prefix.startswith(b"MZ")
        or lowered.startswith((b"<!doctype html", b"<html", b"<svg", b"<script"))
    ):
        return "application/octet-stream"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE":
        return "audio/wav"
    if prefix.startswith(b"OggS"):
        return "audio/ogg"
    if prefix.startswith(b"\x1a\x45\xdf\xa3") and normalized_claim == "audio/webm":
        return "audio/webm"
    if _looks_like_mp3(prefix):
        return "audio/mpeg"
    if prefix.startswith(b"PK\x03\x04"):
        if normalized_claim == (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ):
            # Full package bounds are validated before inert bytes are parsed.
            return normalized_claim
        return "application/zip"
    if normalized_claim in {"text/plain", "text/csv"} and b"\x00" not in prefix:
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return normalized_claim
    return "application/octet-stream"


class AssetService:
    def __init__(self, session: AsyncSession, storage: AssetStorage) -> None:
        self.session = session
        self.storage = storage
        self.repository = AssetRepository(session)

    async def get_by_idempotency_key_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
    ) -> Asset | None:
        try:
            return await self.repository.get_by_idempotency_key_for_owner(
                owner_id,
                idempotency_key,
            )
        except BaseException:
            await self.session.rollback()
            raise

    async def upload_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
        *,
        filename: str | None,
        claimed_media_type: str | None,
        stream: AssetUploadStream,
    ) -> AssetUploadResult:
        asset_id = uuid4()
        writer = await asyncio.to_thread(self.storage.begin_write, asset_id)
        finalized_key: str | None = None
        prefix = bytearray()
        try:
            while True:
                chunk = await stream.read(ASSET_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if len(prefix) < ASSET_SNIFF_BYTES:
                    remaining = ASSET_SNIFF_BYTES - len(prefix)
                    prefix.extend(chunk[:remaining])
                await asyncio.to_thread(writer.write, chunk)
            if writer.byte_size == 0:
                raise AssetEmptyError("asset content is empty")

            original_filename = normalize_original_filename(filename)
            media_type = canonical_media_type(claimed_media_type, bytes(prefix))
            finalized_key = await asyncio.to_thread(writer.finalize)
            try:
                asset = await self.repository.create(
                    asset_id=asset_id,
                    owner_id=owner_id,
                    original_filename=original_filename,
                    media_type=media_type,
                    byte_size=writer.byte_size,
                    content_sha256=writer.content_sha256,
                    storage_key=finalized_key,
                    upload_idempotency_key=idempotency_key,
                )
                await self.session.commit()
                return AssetUploadResult(asset=asset, created=True)
            except IntegrityError:
                await self.session.rollback()
                await self._delete_compensation(finalized_key, asset_id)
                finalized_key = None
                existing = await self.repository.get_by_idempotency_key_for_owner(
                    owner_id,
                    idempotency_key,
                )
                if existing is None:
                    raise
                return AssetUploadResult(asset=existing, created=False)
        except BaseException:
            await self.session.rollback()
            await self._abort_compensation(writer, asset_id)
            if finalized_key is not None:
                await self._delete_compensation(finalized_key, asset_id)
            raise

    async def create_generated_for_owner(
        self,
        owner_id: UUID,
        idempotency_key: UUID,
        *,
        filename: str,
        claimed_media_type: str,
        content: bytes,
        provenance_kind: AssetProvenanceKind,
        source_asset_id: UUID | None,
        runtime_id: str,
        model_id: str,
    ) -> AssetUploadResult:
        if not isinstance(content, bytes):
            raise TypeError("generated asset content must be bytes")
        _validate_generated_provenance(
            provenance_kind,
            source_asset_id,
            runtime_id,
            model_id,
        )
        asset_id = uuid4()
        writer = await asyncio.to_thread(self.storage.begin_write, asset_id)
        finalized_key: str | None = None
        prefix = content[:ASSET_SNIFF_BYTES]
        try:
            for offset in range(0, len(content), ASSET_COPY_CHUNK_BYTES):
                await asyncio.to_thread(
                    writer.write,
                    content[offset : offset + ASSET_COPY_CHUNK_BYTES],
                )
            if writer.byte_size == 0:
                raise AssetEmptyError("asset content is empty")
            original_filename = normalize_original_filename(filename)
            media_type = canonical_media_type(claimed_media_type, prefix)
            if media_type != claimed_media_type:
                raise AssetUploadError("generated asset media type is invalid")
            finalized_key = await asyncio.to_thread(writer.finalize)
            try:
                asset = await self.repository.create_generated(
                    asset_id=asset_id,
                    owner_id=owner_id,
                    original_filename=original_filename,
                    media_type=media_type,
                    byte_size=writer.byte_size,
                    content_sha256=writer.content_sha256,
                    storage_key=finalized_key,
                    upload_idempotency_key=idempotency_key,
                    provenance_kind=provenance_kind,
                    source_asset_id=source_asset_id,
                    runtime_id=runtime_id,
                    model_id=model_id,
                )
                if asset is None:
                    raise AssetProvenanceUnavailableError(
                        "generated asset source is unavailable"
                    )
                await self.session.commit()
                return AssetUploadResult(asset=asset, created=True)
            except IntegrityError:
                await self.session.rollback()
                await self._delete_compensation(finalized_key, asset_id)
                finalized_key = None
                existing = await self.repository.get_by_idempotency_key_for_owner(
                    owner_id,
                    idempotency_key,
                )
                if existing is None:
                    raise
                return AssetUploadResult(asset=existing, created=False)
        except BaseException:
            await self.session.rollback()
            await self._abort_compensation(writer, asset_id)
            if finalized_key is not None:
                await self._delete_compensation(finalized_key, asset_id)
            raise

    async def get_content_for_owner(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> AssetContent | None:
        try:
            asset = await self.repository.get_active_for_owner(owner_id, asset_id)
            if asset is None:
                await self.session.rollback()
                return None
            content = AssetContent(
                storage_key=asset.storage_key,
                original_filename=asset.original_filename,
                media_type=asset.media_type,
                byte_size=asset.byte_size,
            )
            await self.session.rollback()
            return content
        except BaseException:
            await self.session.rollback()
            raise

    async def delete_for_owner(self, owner_id: UUID, asset_id: UUID) -> bool:
        try:
            asset = await self.repository.soft_delete_for_owner(owner_id, asset_id)
            if asset is None:
                await self.session.rollback()
                return False
            storage_key = asset.storage_key
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

        try:
            await asyncio.to_thread(self.storage.delete, storage_key)
        except (StorageError, OSError):
            logger.warning("asset_physical_delete_deferred", asset_id=str(asset_id))
        return True

    async def _delete_compensation(
        self, storage_key: str, asset_id: UUID
    ) -> None:
        try:
            await asyncio.to_thread(self.storage.delete, storage_key)
        except (StorageError, OSError):
            logger.error("asset_upload_compensation_failed", asset_id=str(asset_id))

    @staticmethod
    async def _abort_compensation(
        writer: StagedAssetWrite, asset_id: UUID
    ) -> None:
        try:
            await asyncio.to_thread(writer.abort)
        except (StorageError, OSError):
            logger.error(
                "asset_upload_staging_cleanup_failed", asset_id=str(asset_id)
            )


async def reconcile_asset_storage(
    session_factory: async_sessionmaker[AsyncSession] | None,
    storage: AssetStorage,
) -> ReconciliationReport:
    if session_factory is None:
        return await asyncio.to_thread(
            storage.reconcile,
            active_keys=None,
            deleted_keys=set(),
        )
    async with session_factory() as session:
        state = await AssetRepository(session).list_storage_state()
        await session.rollback()
    return await asyncio.to_thread(
        storage.reconcile,
        active_keys=set(state.active_keys),
        deleted_keys=set(state.deleted_keys),
    )
