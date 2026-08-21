import hashlib
from uuid import uuid4

import pytest

from app.storage.local import (
    LocalAssetStorage,
    StorageKeyError,
    StorageObjectExistsError,
)


def test_staged_write_hashes_counts_finalizes_reads_and_deletes(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    asset_id = uuid4()
    writer = storage.begin_write(asset_id)

    writer.write(b"opaque ")
    writer.write(b"bytes")
    key = writer.finalize()

    assert key == storage.key_for(asset_id)
    assert writer.byte_size == 12
    assert writer.content_sha256 == hashlib.sha256(b"opaque bytes").hexdigest()
    with storage.open_read(key) as stored:
        assert stored.read() == b"opaque bytes"
    assert storage.delete(key) is True
    assert storage.delete(key) is False


def test_finalization_never_overwrites_an_existing_object(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    asset_id = uuid4()
    first = storage.begin_write(asset_id)
    first.write(b"first")
    key = first.finalize()

    second = storage.begin_write(asset_id)
    second.write(b"second")
    with pytest.raises(StorageObjectExistsError):
        second.finalize()
    second.abort()

    with storage.open_read(key) as stored:
        assert stored.read() == b"first"


@pytest.mark.parametrize(
    "key",
    [
        "../secret",
        "/absolute/path",
        "objects/aa/bb/not-a-uuid",
        "objects/aa/bb/00000000000000000000000000000000/extra",
    ],
)
def test_storage_rejects_non_generated_keys(tmp_path, key):
    storage = LocalAssetStorage(tmp_path / "assets")

    with pytest.raises(StorageKeyError):
        storage.open_read(key)


def test_abort_removes_staging_content(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    writer = storage.begin_write(uuid4())
    writer.write(b"partial")

    writer.abort()

    assert list((storage.root / ".staging").iterdir()) == []


def test_reconcile_removes_staging_retries_deleted_and_quarantines_unknown(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    active_writer = storage.begin_write(uuid4())
    active_writer.write(b"active")
    active_key = active_writer.finalize()
    deleted_writer = storage.begin_write(uuid4())
    deleted_writer.write(b"deleted")
    deleted_key = deleted_writer.finalize()
    unknown_writer = storage.begin_write(uuid4())
    unknown_writer.write(b"unknown")
    unknown_key = unknown_writer.finalize()
    staged = storage.begin_write(uuid4())
    staged.write(b"stale")

    report = storage.reconcile(
        active_keys={active_key},
        deleted_keys={deleted_key},
    )

    assert report.removed_staging == 1
    assert report.removed_deleted == 1
    assert report.quarantined_unknown == 1
    with storage.open_read(active_key) as stored:
        assert stored.read() == b"active"
    assert not storage.path_for(deleted_key).exists()
    assert not storage.path_for(unknown_key).exists()
    assert len(list((storage.root / ".quarantine").iterdir())) == 1
