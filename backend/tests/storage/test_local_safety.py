import os
from uuid import uuid4

import pytest

from app.storage.local import (
    LocalAssetStorage,
    StorageObjectMissingError,
    StorageObjectUnsafeError,
)


def test_missing_active_file_is_reported_and_read_is_availability_error(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    key = storage.key_for(uuid4())

    report = storage.reconcile(active_keys={key}, deleted_keys=set())

    assert report.missing_active == (key,)
    with pytest.raises(StorageObjectMissingError):
        storage.open_read(key)


def test_final_symlink_is_never_opened_or_deleted(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    asset_id = uuid4()
    key = storage.key_for(asset_id)
    path = storage.path_for(key)
    path.parent.mkdir(parents=True)
    target = tmp_path / "outside-private"
    target.write_bytes(b"outside")
    path.symlink_to(target)

    with pytest.raises(StorageObjectUnsafeError):
        storage.open_read(key)
    with pytest.raises(StorageObjectUnsafeError):
        storage.delete(key)
    assert target.read_bytes() == b"outside"


def test_symlinked_object_parent_cannot_redirect_finalization(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    asset_id = uuid4()
    key = storage.key_for(asset_id)
    path = storage.path_for(key)
    outside = tmp_path / "outside"
    outside.mkdir()
    first = path.parents[1]
    first.symlink_to(outside, target_is_directory=True)
    writer = storage.begin_write(asset_id)
    writer.write(b"private")

    with pytest.raises(StorageObjectUnsafeError):
        writer.finalize()
    writer.abort()

    assert list(outside.iterdir()) == []
    assert list(storage.staging_root.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_unexpected_fifo_node_is_never_opened_or_deleted(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    key = storage.key_for(uuid4())
    path = storage.path_for(key)
    path.parent.mkdir(parents=True)
    os.mkfifo(path, 0o600)

    with pytest.raises(StorageObjectUnsafeError):
        storage.open_read(key)
    with pytest.raises(StorageObjectUnsafeError):
        storage.delete(key)
    assert path.exists()


def test_reconcile_leaves_unexpected_nodes_in_place_and_reports_them(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    unexpected = storage.staging_root / "unexpected-directory"
    unexpected.mkdir()

    report = storage.reconcile(active_keys=set(), deleted_keys=set())

    assert report.unexpected_nodes == ("unexpected-directory",)
    assert unexpected.is_dir()


def test_reconcile_never_traverses_or_quarantines_through_symlinked_directories(
    tmp_path,
):
    storage = LocalAssetStorage(tmp_path / "assets")
    outside = tmp_path / "outside"
    nested = outside / "bb"
    nested.mkdir(parents=True)
    unknown_id = "aabb" + "c" * 28
    outside_file = nested / unknown_id
    outside_file.write_bytes(b"must remain")
    (storage.objects_root / "aa").symlink_to(outside, target_is_directory=True)

    report = storage.reconcile(active_keys=set(), deleted_keys=set())

    assert "objects/aa" in report.unexpected_nodes
    assert outside_file.read_bytes() == b"must remain"
    assert list(storage.quarantine_root.iterdir()) == []


def test_reconcile_rejects_replaced_top_level_storage_directories(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    marker = outside / "must-remain"
    marker.write_bytes(b"private")
    storage.staging_root.rmdir()
    storage.staging_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(StorageObjectUnsafeError):
        storage.reconcile(active_keys=set(), deleted_keys=set())

    assert marker.read_bytes() == b"private"
