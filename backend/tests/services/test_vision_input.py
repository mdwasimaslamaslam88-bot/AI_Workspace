import asyncio
import base64
import hashlib
import threading
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.vision_input as vision_module
from app.repositories.message import (
    MessageAttachmentClaimError,
    MessageAttachmentMetadata,
)


VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
VALID_JPEG = bytes.fromhex(
    "ffd8ffc00011080001000103011100021100031100ffda0008010100003f00ffd9"
)
from app.services.vision_input import (
    VISION_READ_CHUNK_BYTES,
    VisionInputAttachmentUnavailableError,
    VisionInputContentUnavailableError,
    VisionInputService,
    VisionInputTooLargeError,
    VisionInputUnsupportedError,
)


def _metadata(
    *,
    media_type: str = "image/png",
    content: bytes = b"png-content",
    position: int = 1,
    storage_key: str | None = None,
) -> MessageAttachmentMetadata:
    return MessageAttachmentMetadata(
        asset_id=uuid4(),
        position=position,
        media_type=media_type,
        byte_size=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key or f"objects/{uuid4().hex}",
    )


def _service_with_repository(monkeypatch, metadata):
    repository = Mock(
        list_attachment_metadata_for_owner_message=AsyncMock(
            return_value=metadata
        )
    )
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(vision_module, "MessageRepository", repository_factory)
    session = AsyncMock(spec=AsyncSession)
    service = VisionInputService(session, None)
    return service, repository, repository_factory, session


@pytest.mark.asyncio
async def test_resolution_uses_exact_owner_conversation_message_and_order(
    monkeypatch,
):
    owner_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    first = _metadata(media_type="image/png", position=1)
    second = _metadata(media_type="image/jpeg", position=2)
    service, repository, repository_factory, session = _service_with_repository(
        monkeypatch,
        (first, second),
    )

    result = await service.resolve_for_owner_message(
        owner_id,
        conversation_id,
        message_id,
        (first.asset_id, second.asset_id),
    )

    assert result == (first, second)
    repository_factory.assert_called_once_with(session)
    repository.list_attachment_metadata_for_owner_message.assert_awaited_once_with(
        owner_id,
        conversation_id,
        message_id,
        (first.asset_id, second.asset_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type",
    ["image/gif", "image/webp", "image/svg+xml"],
)
async def test_unsupported_image_metadata_is_rejected(monkeypatch, media_type):
    item = _metadata(media_type=media_type)
    service, *_unused = _service_with_repository(monkeypatch, (item,))

    with pytest.raises(
        VisionInputUnsupportedError,
        match="vision attachments are unsupported",
    ):
        await service.resolve_for_owner_message(
            uuid4(), uuid4(), uuid4(), (item.asset_id,)
        )


@pytest.mark.asyncio
async def test_mixed_image_and_opaque_metadata_is_rejected(monkeypatch):
    image = _metadata(media_type="image/png", position=1)
    opaque = _metadata(media_type="application/pdf", position=2)
    service, *_unused = _service_with_repository(monkeypatch, (image, opaque))

    with pytest.raises(VisionInputUnsupportedError):
        await service.resolve_for_owner_message(
            uuid4(),
            uuid4(),
            uuid4(),
            (image.asset_id, opaque.asset_id),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type",
    ["application/octet-stream", "application/pdf", "audio/wav"],
)
async def test_opaque_only_metadata_preserves_text_generation(
    monkeypatch,
    media_type,
):
    item = _metadata(media_type=media_type)
    service, *_unused = _service_with_repository(monkeypatch, (item,))

    result = await service.resolve_for_owner_message(
        uuid4(), uuid4(), uuid4(), (item.asset_id,)
    )

    assert result == ()


@pytest.mark.asyncio
async def test_repository_owner_or_relation_mismatch_is_generic(monkeypatch):
    service, repository, *_unused = _service_with_repository(monkeypatch, ())
    repository.list_attachment_metadata_for_owner_message.side_effect = (
        MessageAttachmentClaimError("private relation detail")
    )

    with pytest.raises(VisionInputAttachmentUnavailableError) as captured:
        await service.resolve_for_owner_message(
            uuid4(), uuid4(), uuid4(), (uuid4(),)
        )

    assert str(captured.value) == "vision attachments are unavailable"
    assert "private relation detail" not in str(captured.value)


def test_placeholder_sizes_are_exact_and_aggregate_bounded():
    first = _metadata(content=b"a")
    second = _metadata(content=b"bc", position=2)

    result = VisionInputService.placeholder_images((first, second), 8)

    assert result == ("AAAA", "AAAA")
    with pytest.raises(VisionInputTooLargeError):
        VisionInputService.placeholder_images((first, second), 7)


class _TrackingHandle:
    def __init__(self, path: Path) -> None:
        self._handle = path.open("rb")
        self.read_sizes: list[int] = []
        self.closed = False

    def fileno(self):
        return self._handle.fileno()

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self._handle.read(size)

    def close(self):
        self.closed = True
        self._handle.close()


class _Storage:
    def __init__(self, open_read) -> None:
        self._open_read = open_read
        self.keys: list[str] = []

    def open_read(self, storage_key):
        self.keys.append(storage_key)
        return self._open_read()


@pytest.mark.asyncio
async def test_encoding_is_incremental_ordered_verified_and_closes_handles(
    tmp_path,
):
    first_content = VALID_PNG
    second_content = VALID_JPEG
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.write_bytes(first_content)
    second_path.write_bytes(second_content)
    handles = [_TrackingHandle(first_path), _TrackingHandle(second_path)]
    first = _metadata(content=first_content, position=1, storage_key="first-key")
    second = _metadata(
        media_type="image/jpeg",
        content=second_content,
        position=2,
        storage_key="second-key",
    )
    returned_handles = []

    def open_handle():
        handle = handles.pop(0)
        returned_handles.append(handle)
        return handle

    storage = _Storage(open_handle)
    service = VisionInputService(AsyncMock(spec=AsyncSession), storage)

    result = await service.encode_images((first, second))

    assert result == (
        base64.b64encode(first_content).decode("ascii"),
        base64.b64encode(second_content).decode("ascii"),
    )
    assert storage.keys == ["first-key", "second-key"]
    assert all(handle.closed for handle in returned_handles)
    assert all(
        size == VISION_READ_CHUNK_BYTES
        for handle in returned_handles
        for size in handle.read_sizes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "content"),
    [
        ("image/png", b"not-a-png"),
        ("image/png", b"\x89PNG\r\n\x1a\ntruncated"),
        ("image/jpeg", b"not-a-jpeg"),
        ("image/jpeg", b"\xff\xd8\xff\xd9"),
    ],
)
async def test_malformed_image_content_is_rejected_before_runtime(
    tmp_path,
    media_type,
    content,
):
    path = tmp_path / "malformed-image"
    path.write_bytes(content)
    handle = _TrackingHandle(path)
    service = VisionInputService(
        AsyncMock(spec=AsyncSession),
        _Storage(lambda: handle),
    )

    with pytest.raises(VisionInputContentUnavailableError) as captured:
        await service.encode_images(
            (_metadata(media_type=media_type, content=content),)
        )

    assert str(captured.value) == "vision input content is unavailable"
    assert handle.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["size", "checksum", "missing", "unsafe"])
async def test_file_failures_are_generic_and_close_open_handles(
    tmp_path,
    failure,
):
    content = b"verified-content"
    path = tmp_path / "asset"
    path.write_bytes(content)
    handle = _TrackingHandle(path)

    if failure == "missing":
        storage = _Storage(
            lambda: (_ for _ in ()).throw(FileNotFoundError("private path"))
        )
        metadata = _metadata(content=content)
    elif failure == "unsafe":
        storage = _Storage(
            lambda: (_ for _ in ()).throw(OSError("private symlink detail"))
        )
        metadata = _metadata(content=content)
    else:
        storage = _Storage(lambda: handle)
        metadata = _metadata(content=content)
        if failure == "size":
            metadata = MessageAttachmentMetadata(
                asset_id=metadata.asset_id,
                position=metadata.position,
                media_type=metadata.media_type,
                byte_size=metadata.byte_size + 1,
                content_sha256=metadata.content_sha256,
                storage_key=metadata.storage_key,
            )
        else:
            metadata = MessageAttachmentMetadata(
                asset_id=metadata.asset_id,
                position=metadata.position,
                media_type=metadata.media_type,
                byte_size=metadata.byte_size,
                content_sha256="0" * 64,
                storage_key=metadata.storage_key,
            )
    service = VisionInputService(AsyncMock(spec=AsyncSession), storage)

    with pytest.raises(VisionInputContentUnavailableError) as captured:
        await service.encode_images((metadata,))

    assert str(captured.value) == "vision input content is unavailable"
    assert "private" not in str(captured.value)
    if failure in {"size", "checksum"}:
        assert handle.closed


@pytest.mark.asyncio
async def test_non_regular_device_is_rejected_and_closed():
    handle = open("/dev/null", "rb")
    storage = _Storage(lambda: handle)
    service = VisionInputService(AsyncMock(spec=AsyncSession), storage)

    with pytest.raises(VisionInputContentUnavailableError):
        await service.encode_images((_metadata(content=b"x"),))

    assert handle.closed


class _FailingHandle(_TrackingHandle):
    def read(self, size=-1):
        self.read_sizes.append(size)
        raise OSError("private read failure")


@pytest.mark.asyncio
async def test_read_failure_closes_handle_without_leaking_detail(tmp_path):
    content = b"content"
    path = tmp_path / "asset"
    path.write_bytes(content)
    handle = _FailingHandle(path)
    service = VisionInputService(
        AsyncMock(spec=AsyncSession),
        _Storage(lambda: handle),
    )

    with pytest.raises(VisionInputContentUnavailableError) as captured:
        await service.encode_images((_metadata(content=content),))

    assert handle.closed
    assert "private read failure" not in str(captured.value)


class _BlockingHandle(_TrackingHandle):
    def __init__(self, path: Path, entered, release) -> None:
        super().__init__(path)
        self.entered = entered
        self.release = release

    def read(self, size=-1):
        self.read_sizes.append(size)
        self.entered.set()
        self.release.wait(timeout=2)
        return self._handle.read(size)


@pytest.mark.asyncio
async def test_cancellation_waits_for_read_and_closes_handle(tmp_path):
    content = b"content"
    path = tmp_path / "asset"
    path.write_bytes(content)
    entered = threading.Event()
    release = threading.Event()
    handle = _BlockingHandle(path, entered, release)
    service = VisionInputService(
        AsyncMock(spec=AsyncSession),
        _Storage(lambda: handle),
    )
    task = asyncio.create_task(
        service.encode_images((_metadata(content=content),))
    )
    assert await asyncio.to_thread(entered.wait, 1)

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handle.closed


@pytest.mark.asyncio
async def test_unconfigured_storage_is_generic_without_open_attempt():
    service = VisionInputService(AsyncMock(spec=AsyncSession), None)

    with pytest.raises(VisionInputContentUnavailableError) as captured:
        await service.encode_images((_metadata(),))

    assert str(captured.value) == "vision input content is unavailable"


@pytest.mark.asyncio
async def test_failures_log_no_storage_key_image_bytes_or_base64(caplog):
    content = b"sentinel-private-image-bytes"
    encoded = base64.b64encode(content).decode("ascii")
    storage_key = "objects/sentinel-private-storage-key"
    service = VisionInputService(
        AsyncMock(spec=AsyncSession),
        _Storage(
            lambda: (_ for _ in ()).throw(OSError("sentinel-private-path"))
        ),
    )

    with pytest.raises(VisionInputContentUnavailableError):
        await service.encode_images(
            (_metadata(content=content, storage_key=storage_key),)
        )

    safe_output = caplog.text
    assert storage_key not in safe_output
    assert content.decode("ascii") not in safe_output
    assert encoded not in safe_output
    assert "sentinel-private-path" not in safe_output
