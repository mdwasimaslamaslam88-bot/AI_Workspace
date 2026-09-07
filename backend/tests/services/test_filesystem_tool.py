import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app.services.filesystem_tool import (
    FilesystemToolError,
    MAX_FILESYSTEM_WRITE_BYTES,
    OwnerFilesystemWorkspace,
)


@pytest.fixture
def workspace(tmp_path: Path) -> OwnerFilesystemWorkspace:
    root = tmp_path / "configured-root"
    root.mkdir(mode=0o700)
    return OwnerFilesystemWorkspace((root,))


def test_special_file_read_is_rejected_without_blocking_open(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    subprocess.run(
        [sys.executable, "-c", """
import os, sys
from app.services.filesystem_tool import OwnerFilesystemWorkspace, FilesystemToolError
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    try:
        OwnerFilesystemWorkspace._read_at(fd, 'pipe', 100)
    except FilesystemToolError:
        pass
    else:
        raise AssertionError('special file accepted')
finally:
    os.close(fd)
""", str(tmp_path)],
        check=True,
        timeout=3,
        capture_output=True,
    )


def test_write_read_exists_stat_list_are_real_bounded_and_owner_scoped(
    workspace: OwnerFilesystemWorkspace,
):
    owner = uuid4()
    other_owner = uuid4()

    written = workspace.write(owner, "AI_OS_REAL_TEST.txt", "verified")
    repeated = workspace.write(owner, "AI_OS_REAL_TEST.txt", "verified")
    read = workspace.read(owner, "AI_OS_REAL_TEST.txt")
    exists = workspace.exists(owner, "AI_OS_REAL_TEST.txt")
    stat = workspace.stat(owner, "AI_OS_REAL_TEST.txt")
    listed = workspace.list(owner)
    other = workspace.exists(other_owner, "AI_OS_REAL_TEST.txt")

    assert written.payload["changed"] is True
    assert repeated.payload["changed"] is False
    assert read.payload["content"] == "verified"
    assert exists.payload == {"exists": True}
    assert stat.payload["kind"] == "file"
    assert listed.payload["entries"] == [
        {"name": "AI_OS_REAL_TEST.txt", "kind": "file", "size": 8}
    ]
    assert other.payload == {"exists": False}


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../escape.txt",
        "folder/../../escape.txt",
        "~/escape.txt",
        ".env",
        ".hidden/value.txt",
        "secrets/value.txt",
        "private.pem",
        "folder\\escape.txt",
    ],
)
def test_paths_outside_narrow_workspace_or_sensitive_paths_are_denied(
    workspace: OwnerFilesystemWorkspace,
    path: str,
):
    with pytest.raises(FilesystemToolError):
        workspace.write(uuid4(), path, "safe")


def test_symlink_parent_and_target_escape_are_denied(
    workspace: OwnerFilesystemWorkspace,
    tmp_path: Path,
):
    owner = uuid4()
    workspace.exists(owner, "seed.txt")
    owner_root = tmp_path / "configured-root" / str(owner)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("outside", encoding="utf-8")
    os.symlink(outside, owner_root / "escape")
    os.symlink(outside / "data.txt", owner_root / "linked.txt")

    with pytest.raises(FilesystemToolError):
        workspace.read(owner, "escape/data.txt")
    with pytest.raises(FilesystemToolError):
        workspace.read(owner, "linked.txt")


def test_large_or_secret_shaped_content_is_denied(
    workspace: OwnerFilesystemWorkspace,
):
    owner = uuid4()
    with pytest.raises(FilesystemToolError):
        workspace.write(owner, "large.txt", "x" * (MAX_FILESYSTEM_WRITE_BYTES + 1))
    with pytest.raises(FilesystemToolError):
        workspace.write(owner, "auth.txt", "password=not-a-placeholder-secret")


def test_existing_file_cannot_be_overwritten_with_different_content(
    workspace: OwnerFilesystemWorkspace,
):
    owner = uuid4()
    workspace.write(owner, "immutable.txt", "first")

    with pytest.raises(FilesystemToolError):
        workspace.write(owner, "immutable.txt", "second")

    assert workspace.read(owner, "immutable.txt").payload["content"] == "first"


def test_publish_race_cannot_overwrite_an_existing_target(
    workspace: OwnerFilesystemWorkspace,
    tmp_path: Path,
    monkeypatch,
):
    owner = uuid4()
    workspace.exists(owner, "seed.txt")
    owner_root = tmp_path / "configured-root" / str(owner)
    target = owner_root / "raced.txt"
    target.write_text("first", encoding="utf-8")
    monkeypatch.setattr(workspace, "_stat_at", lambda _fd, _name: None)

    with pytest.raises(FilesystemToolError):
        workspace.write(owner, "raced.txt", "second")

    assert target.read_text(encoding="utf-8") == "first"
    assert not any(item.name.startswith(".ai-os-") for item in owner_root.iterdir())
