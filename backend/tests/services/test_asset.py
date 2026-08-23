import asyncio
import hashlib
from io import BytesIO
import wave
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, AssetProvenanceKind
from app.repositories.asset import AssetRepository
from app.services.asset import AssetService, canonical_media_type, normalize_original_filename
from app.storage.local import LocalAssetStorage, StorageError


class ChunkStream:
    def __init__(self, *chunks: bytes, failure: BaseException | None = None):
        self.chunks = list(chunks)
        self.failure = failure

    async def read(self, _size=-1):
        if self.chunks:
            return self.chunks.pop(0)
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        return b""


def _asset(owner_id, asset_id, storage_key, *, idempotency_key) -> Asset:
    return Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="résumé.txt",
        media_type="text/plain",
        byte_size=7,
        content_sha256=hashlib.sha256(b"content").hexdigest(),
        storage_key=storage_key,
        upload_idempotency_key=idempotency_key,
        created_at=datetime.now(timezone.utc),
    )


def _service(tmp_path):
    session = AsyncMock(spec=AsyncSession)
    storage = LocalAssetStorage(tmp_path / "assets")
    service = AssetService(session, storage)
    service.repository = AsyncMock(spec=AssetRepository)
    return service, session, storage


def _wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\0\0" * 1_600)
    return output.getvalue()


@pytest.mark.asyncio
async def test_upload_streams_hashes_counts_normalizes_and_commits(tmp_path):
    service, session, storage = _service(tmp_path)
    owner_id = uuid4()
    key = uuid4()

    async def create(**values):
        return _asset(owner_id, values["asset_id"], values["storage_key"], idempotency_key=key)

    service.repository.create.side_effect = create
    result = await service.upload_for_owner(
        owner_id,
        key,
        filename="folder\\re\u0301sume\u0301.txt",
        claimed_media_type="text/plain; charset=utf-8",
        stream=ChunkStream(b"con", b"tent"),
    )

    assert result.created is True
    values = service.repository.create.await_args.kwargs
    assert values["original_filename"] == "résumé.txt"
    assert values["media_type"] == "text/plain"
    assert values["byte_size"] == 7
    assert values["content_sha256"] == hashlib.sha256(b"content").hexdigest()
    assert values["storage_key"] == storage.key_for(values["asset_id"])
    with storage.open_read(values["storage_key"]) as handle:
        assert handle.read() == b"content"
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_database_failure_after_finalize_removes_final_object(tmp_path):
    service, session, storage = _service(tmp_path)
    service.repository.create.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.upload_for_owner(
            uuid4(),
            uuid4(),
            filename="private.txt",
            claimed_media_type="text/plain",
            stream=ChunkStream(b"private"),
        )

    assert list(storage.staging_root.iterdir()) == []
    assert [
        path for path in storage.objects_root.rglob("*") if path.is_file()
    ] == []
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_cancellation_aborts_staging_without_final_content(tmp_path):
    service, _session, storage = _service(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await service.upload_for_owner(
            uuid4(),
            uuid4(),
            filename="cancel.txt",
            claimed_media_type="text/plain",
            stream=ChunkStream(b"partial", failure=asyncio.CancelledError()),
        )

    assert list(storage.staging_root.iterdir()) == []
    assert [
        path for path in storage.objects_root.rglob("*") if path.is_file()
    ] == []


@pytest.mark.asyncio
async def test_integrity_race_returns_winner_and_removes_loser_bytes(tmp_path):
    service, session, storage = _service(tmp_path)
    owner_id = uuid4()
    key = uuid4()
    winner_id = uuid4()
    writer = storage.begin_write(winner_id)
    writer.write(b"content")
    winner_key = writer.finalize()
    winner = _asset(owner_id, winner_id, winner_key, idempotency_key=key)
    service.repository.create.side_effect = IntegrityError("insert", {}, Exception())
    service.repository.get_by_idempotency_key_for_owner.return_value = winner

    result = await service.upload_for_owner(
        owner_id,
        key,
        filename="same.txt",
        claimed_media_type="text/plain",
        stream=ChunkStream(b"content"),
    )

    assert result == type(result)(asset=winner, created=False)
    final_files = [path for path in storage.objects_root.rglob("*") if path.is_file()]
    assert final_files == [storage.path_for(winner_key)]
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_generated_speech_has_explicit_provenance_and_private_bytes(tmp_path):
    service, session, storage = _service(tmp_path)
    owner_id = uuid4()
    idempotency_key = uuid4()
    model_id = "piper:" + "a" * 24

    async def create_generated(**values):
        values["id"] = values.pop("asset_id")
        return Asset(**values)

    service.repository.create_generated.side_effect = create_generated
    result = await service.create_generated_for_owner(
        owner_id,
        idempotency_key,
        filename="speech.wav",
        claimed_media_type="audio/wav",
        content=_wav_bytes(),
        provenance_kind=AssetProvenanceKind.SPEECH_SYNTHESIS,
        source_asset_id=None,
        runtime_id="piper",
        model_id=model_id,
    )

    values = service.repository.create_generated.await_args.kwargs
    assert result.created is True
    assert values["provenance_kind"] is AssetProvenanceKind.SPEECH_SYNTHESIS
    assert values["runtime_id"] == "piper"
    assert values["model_id"] == model_id
    with storage.open_read(values["storage_key"]) as handle:
        assert handle.read() == _wav_bytes()
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_generated_edit_rejects_unowned_source_and_removes_output(tmp_path):
    service, session, storage = _service(tmp_path)
    service.repository.create_generated.return_value = None

    from app.services.asset import AssetProvenanceUnavailableError

    with pytest.raises(AssetProvenanceUnavailableError):
        await service.create_generated_for_owner(
            uuid4(),
            uuid4(),
            filename="edited.png",
            claimed_media_type="image/png",
            content=(
                b"\x89PNG\r\n\x1a\n"
                b"bounded-fixture-content"
            ),
            provenance_kind=AssetProvenanceKind.IMAGE_EDITING,
            source_asset_id=uuid4(),
            runtime_id="comfyui",
            model_id="comfyui:" + "b" * 24,
        )

    assert not [path for path in storage.objects_root.rglob("*") if path.is_file()]
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_delete_commits_tombstone_before_physical_cleanup(tmp_path):
    service, session, storage = _service(tmp_path)
    owner_id = uuid4()
    asset_id = uuid4()
    writer = storage.begin_write(asset_id)
    writer.write(b"content")
    storage_key = writer.finalize()
    asset = _asset(owner_id, asset_id, storage_key, idempotency_key=uuid4())
    service.repository.soft_delete_for_owner.return_value = asset
    events = []
    session.commit.side_effect = lambda: events.append("commit")
    original_delete = storage.delete
    storage.delete = Mock(side_effect=lambda key: (events.append("delete"), original_delete(key))[1])

    assert await service.delete_for_owner(owner_id, asset_id) is True
    assert events == ["commit", "delete"]
    assert not storage.path_for(storage_key).exists()


@pytest.mark.asyncio
async def test_physical_delete_failure_keeps_successful_tombstone(tmp_path):
    service, session, _storage = _service(tmp_path)
    owner_id = uuid4()
    asset_id = uuid4()
    asset = _asset(owner_id, asset_id, "objects/aa/aa/" + "a" * 32, idempotency_key=uuid4())
    service.repository.soft_delete_for_owner.return_value = asset
    service.storage.delete = Mock(side_effect=StorageError("disk failure"))

    assert await service.delete_for_owner(owner_id, asset_id) is True
    session.commit.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("claimed", "prefix", "expected"),
    [
        ("image/svg+xml", b"<svg onload=alert(1)>", "application/octet-stream"),
        ("text/html", b"<!doctype html>", "application/octet-stream"),
        ("image/png", b"not a png", "application/octet-stream"),
        ("application/pdf", b"%PDF-1.7", "application/pdf"),
        ("audio/webm", b"\x1a\x45\xdf\xa3fixture", "audio/webm"),
        ("text/csv", b"a,b\n1,2", "text/csv"),
        ("text/plain", b"\xff\xfe", "application/octet-stream"),
        ("application/x-msdownload", b"MZpayload", "application/octet-stream"),
    ],
)
def test_media_type_policy_never_makes_dangerous_or_mismatched_content_inline(
    claimed,
    prefix,
    expected,
):
    assert canonical_media_type(claimed, prefix) == expected


@pytest.mark.parametrize("filename", ["bad\x00.txt", "bad\r\nheader.txt", "bad\u202etxt"])
def test_filename_controls_are_rejected(filename):
    with pytest.raises(ValueError):
        normalize_original_filename(filename)
