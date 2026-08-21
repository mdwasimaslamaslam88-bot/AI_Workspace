from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import stat
from collections.abc import Callable
from typing import BinaryIO, TypeVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.message import (
    MessageAttachmentClaimError,
    MessageAttachmentMetadata,
    MessageRepository,
)
from app.storage.base import AssetStorage


VISION_READ_CHUNK_BYTES = 65_536
VISION_MEDIA_TYPES = frozenset({"image/jpeg", "image/png"})
_T = TypeVar("_T")


class VisionInputAttachmentUnavailableError(RuntimeError):
    """The requested attachments are not active on the new owned message."""


class VisionInputUnsupportedError(RuntimeError):
    """The new message does not contain one supported homogeneous image set."""


class VisionInputContentUnavailableError(RuntimeError):
    """An authorized image cannot be read and verified safely."""


class VisionInputTooLargeError(RuntimeError):
    """The encoded image input cannot fit the bounded runtime request."""


def _base64_size(byte_size: int) -> int:
    if isinstance(byte_size, bool) or not isinstance(byte_size, int):
        raise TypeError("vision asset byte size must be an integer")
    if byte_size < 1:
        raise ValueError("vision asset byte size must be positive")
    return 4 * ((byte_size + 2) // 3)


async def _finish_thread_call(task: asyncio.Task[_T]) -> _T:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _thread_call(function: Callable[[], _T]) -> _T:
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await _finish_thread_call(task)
        except Exception:
            pass
        raise


async def _open_read(storage: AssetStorage, storage_key: str) -> BinaryIO:
    task = asyncio.create_task(asyncio.to_thread(storage.open_read, storage_key))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        handle: BinaryIO | None = None
        try:
            handle = await _finish_thread_call(task)
        except Exception:
            pass
        if handle is not None:
            handle.close()
        raise


class VisionInputService:
    def __init__(
        self,
        session: AsyncSession,
        storage: AssetStorage | None,
    ) -> None:
        self.repository = MessageRepository(session)
        self.storage = storage

    async def resolve_for_owner_message(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        attachment_ids: tuple[UUID, ...],
    ) -> tuple[MessageAttachmentMetadata, ...]:
        try:
            metadata = (
                await self.repository.list_attachment_metadata_for_owner_message(
                    owner_id,
                    conversation_id,
                    message_id,
                    attachment_ids,
                )
            )
        except MessageAttachmentClaimError as exc:
            raise VisionInputAttachmentUnavailableError(
                "vision attachments are unavailable"
            ) from exc

        eligible = tuple(
            item for item in metadata if item.media_type in VISION_MEDIA_TYPES
        )
        if eligible:
            if len(eligible) != len(metadata):
                raise VisionInputUnsupportedError(
                    "vision attachments are unsupported"
                )
            return metadata
        if any(item.media_type.startswith("image/") for item in metadata):
            raise VisionInputUnsupportedError(
                "vision attachments are unsupported"
            )
        return ()

    @staticmethod
    def placeholder_images(
        metadata: tuple[MessageAttachmentMetadata, ...],
        max_request_bytes: int,
    ) -> tuple[str, ...]:
        if (
            isinstance(max_request_bytes, bool)
            or not isinstance(max_request_bytes, int)
            or max_request_bytes < 1
        ):
            raise ValueError("runtime request bound must be a positive integer")
        remaining = max_request_bytes
        sizes: list[int] = []
        for item in metadata:
            encoded_size = _base64_size(item.byte_size)
            if encoded_size > remaining:
                raise VisionInputTooLargeError(
                    "vision input is too large"
                )
            sizes.append(encoded_size)
            remaining -= encoded_size
        return tuple("A" * encoded_size for encoded_size in sizes)

    async def encode_images(
        self,
        metadata: tuple[MessageAttachmentMetadata, ...],
    ) -> tuple[str, ...]:
        if self.storage is None:
            raise VisionInputContentUnavailableError(
                "vision input content is unavailable"
            )
        encoded: list[str] = []
        try:
            for item in metadata:
                encoded.append(await self._encode_image(item))
            return tuple(encoded)
        except asyncio.CancelledError:
            raise
        except VisionInputContentUnavailableError:
            raise
        except Exception:
            raise VisionInputContentUnavailableError(
                "vision input content is unavailable"
            ) from None

    async def _encode_image(self, metadata: MessageAttachmentMetadata) -> str:
        if self.storage is None:  # pragma: no cover - guarded by encode_images
            raise VisionInputContentUnavailableError(
                "vision input content is unavailable"
            )
        handle: BinaryIO | None = None
        encoded = bytearray()
        carry = b""
        digest = hashlib.sha256()
        byte_size = 0
        try:
            handle = await _open_read(self.storage, metadata.storage_key)
            details = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_size != metadata.byte_size
            ):
                raise VisionInputContentUnavailableError(
                    "vision input content is unavailable"
                )

            while True:
                chunk = await _thread_call(
                    lambda: handle.read(VISION_READ_CHUNK_BYTES)
                )
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise VisionInputContentUnavailableError(
                        "vision input content is unavailable"
                    )
                byte_size += len(chunk)
                if byte_size > metadata.byte_size:
                    raise VisionInputContentUnavailableError(
                        "vision input content is unavailable"
                    )
                digest.update(chunk)
                combined = carry + chunk
                complete_size = (len(combined) // 3) * 3
                if complete_size:
                    encoded.extend(base64.b64encode(combined[:complete_size]))
                carry = combined[complete_size:]

            if carry:
                encoded.extend(base64.b64encode(carry))
            if (
                byte_size != metadata.byte_size
                or not hmac.compare_digest(
                    digest.hexdigest(),
                    metadata.content_sha256,
                )
                or len(encoded) != _base64_size(metadata.byte_size)
            ):
                raise VisionInputContentUnavailableError(
                    "vision input content is unavailable"
                )
            return encoded.decode("ascii")
        except asyncio.CancelledError:
            raise
        except VisionInputContentUnavailableError:
            raise
        except Exception:
            raise VisionInputContentUnavailableError(
                "vision input content is unavailable"
            ) from None
        finally:
            carry = b""
            encoded.clear()
            if handle is not None:
                try:
                    await _thread_call(handle.close)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
