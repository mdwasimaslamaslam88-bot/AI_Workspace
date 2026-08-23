import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings


_TOOL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backup_tool.py"
_SPEC = importlib.util.spec_from_file_location("work_station_backup_tool", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
backup_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup_tool)


def _write_verified_fixture(root: Path, *, unsafe_member: str | None = None) -> Path:
    backup = root / "work-station-test"
    backup.mkdir()
    (backup / "database.dump").write_bytes(b"bounded postgres archive")
    with tarfile.open(backup / "assets.tar.gz", "w:gz") as archive:
        data = b"private owner asset"
        member = tarfile.TarInfo(unsafe_member or "assets/document.txt")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    manifest = {
        "format_version": 1,
        "created_at": "2026-08-23T00:00:00+00:00",
        "application_commit": "a" * 40,
        "components": ["database", "assets"],
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    backup_tool._write_checksums(
        backup,
        ["database.dump", "assets.tar.gz", "manifest.json"],
    )
    return backup


def test_database_command_never_contains_password(monkeypatch):
    configured = Settings(_env_file=None, DATABASE_SSL_MODE="disable")
    url = make_url(
        "postgresql://owner:private-password@127.0.0.1:5432/work_station"
    )

    arguments = backup_tool._database_arguments(url)
    environment = backup_tool._command_environment(url, configured)

    assert "private-password" not in " ".join(arguments)
    assert environment["PGPASSWORD"] == "private-password"
    assert environment["PGSSLMODE"] == "disable"


def test_backup_verification_checks_dump_archive_and_integrity(tmp_path, monkeypatch):
    backup = _write_verified_fixture(tmp_path)
    monkeypatch.setattr(backup_tool, "_run_private", lambda *_args: None)
    manifest = backup_tool.verify_backup(backup)
    assert manifest["components"] == ["database", "assets"]

    (backup / "database.dump").write_bytes(b"tampered")
    with pytest.raises(backup_tool.BackupError, match="checksum"):
        backup_tool.verify_backup(backup)


def test_backup_verification_rejects_archive_traversal(tmp_path, monkeypatch):
    backup = _write_verified_fixture(tmp_path, unsafe_member="../private.txt")
    monkeypatch.setattr(backup_tool, "_run_private", lambda *_args: None)

    with pytest.raises(backup_tool.BackupError, match="unsafe entry"):
        backup_tool.verify_backup(backup)


def test_restore_refuses_the_configured_application_database(monkeypatch, tmp_path):
    configured = Settings(
        _env_file=None,
        DATABASE_URL=(
            "postgresql+asyncpg://owner:private-password@127.0.0.1:5432/work_station"
        ),
        DATABASE_SSL_MODE="disable",
    )
    monkeypatch.setattr(backup_tool, "_settings", lambda: configured)
    monkeypatch.setenv("WORK_STATION_CONFIRM_DISPOSABLE_RESTORE", "YES")
    monkeypatch.setenv(
        "WORK_STATION_RESTORE_DATABASE_URL",
        "postgresql://owner:different-password@127.0.0.1:5432/work_station",
    )

    with pytest.raises(backup_tool.BackupError, match="refuses"):
        backup_tool.restore_backup(tmp_path, None)


def test_created_backup_is_verified_before_it_is_published(monkeypatch, tmp_path):
    destination = tmp_path / "backups"
    destination.mkdir(mode=0o700)
    configured = Settings(
        _env_file=None,
        DATABASE_URL=(
            "postgresql+asyncpg://owner:private-password@127.0.0.1:5432/work_station"
        ),
        DATABASE_SSL_MODE="disable",
    )
    commands: list[str] = []

    def run_private(command, _environment):
        commands.append(command[0])
        if command[0] == "pg_dump":
            output = Path(command[command.index("--file") + 1])
            output.write_bytes(b"bounded postgres archive")

    monkeypatch.setattr(backup_tool, "_settings", lambda: configured)
    monkeypatch.setattr(backup_tool, "_run_private", run_private)

    created = backup_tool.create_backup(destination)

    assert created.parent == destination
    assert created.is_dir()
    assert commands == ["pg_dump", "pg_restore"]
    assert not list(destination.glob(".work-station-backup-*"))


def test_backup_destination_must_be_private_and_outside_assets(monkeypatch, tmp_path):
    asset_root = tmp_path / "assets"
    asset_root.mkdir(mode=0o700)
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://owner:password@127.0.0.1/work_station",
        ASSET_STORAGE_ROOT=asset_root,
    )
    monkeypatch.setattr(backup_tool, "_settings", lambda: configured)

    permissive = tmp_path / "permissive"
    permissive.mkdir(mode=0o755)
    with pytest.raises(backup_tool.BackupError, match="owner-only"):
        backup_tool.create_backup(permissive)

    linked = tmp_path / "linked-backups"
    linked.symlink_to(asset_root, target_is_directory=True)
    with pytest.raises(backup_tool.BackupError, match="symbolic link"):
        backup_tool.create_backup(linked)

    nested = asset_root / "backups"
    nested.mkdir(mode=0o700)
    with pytest.raises(backup_tool.BackupError, match="outside the asset tree"):
        backup_tool.create_backup(nested)
