#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

from sqlalchemy.engine import URL, make_url


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402


FORMAT_VERSION = 1
MAX_ARCHIVE_MEMBERS = 1_000_000


class BackupError(RuntimeError):
    pass


def _settings() -> Settings:
    return Settings(_env_file=BACKEND_ROOT / ".env")


def _command_environment(url: URL, configured: Settings) -> dict[str, str]:
    environment = os.environ.copy()
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    environment["PGSSLMODE"] = configured.DATABASE_SSL_MODE
    if configured.DATABASE_SSL_ROOT_CERT is not None:
        environment["PGSSLROOTCERT"] = configured.DATABASE_SSL_ROOT_CERT
    return environment


def _database_arguments(url: URL) -> list[str]:
    if url.host is None or url.database is None or url.username is None:
        raise BackupError("The configured database URL is incomplete.")
    return [
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        url.username,
        "--dbname",
        url.database,
    ]


def _run_private(command: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise BackupError("A PostgreSQL backup or restore command failed.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else "unknown"


def _validate_asset_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("The configured asset storage root is not a safe directory.")
    for item in root.rglob("*"):
        if item.is_symlink():
            raise BackupError("Asset backups refuse symbolic links.")


def _write_asset_archive(asset_root: Path, destination: Path) -> None:
    _validate_asset_tree(asset_root)
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(asset_root, arcname="assets", recursive=True)


def _write_checksums(directory: Path, names: list[str]) -> None:
    content = "".join(f"{_sha256(directory / name)}  {name}\n" for name in names)
    (directory / "SHA256SUMS").write_text(content, encoding="ascii")


def _validate_backup_destination(destination: Path, configured: Settings) -> Path:
    expanded_destination = destination.expanduser()
    if expanded_destination.is_symlink():
        raise BackupError("The backup destination must not be a symbolic link.")
    resolved_destination = expanded_destination.resolve()
    if not resolved_destination.is_dir():
        raise BackupError("The backup destination must be an existing directory.")
    if (
        resolved_destination == REPOSITORY_ROOT
        or REPOSITORY_ROOT in resolved_destination.parents
    ):
        raise BackupError("Backups must be stored outside the source tree.")
    if stat.S_IMODE(resolved_destination.stat().st_mode) & 0o077:
        raise BackupError("The backup destination must be owner-only.")

    if configured.ASSET_STORAGE_ROOT is not None:
        asset_root = configured.ASSET_STORAGE_ROOT.expanduser().resolve()
        if (
            asset_root == resolved_destination
            or asset_root in resolved_destination.parents
        ):
            raise BackupError("Backups must be stored outside the asset tree.")
    return resolved_destination


def create_backup(destination: Path) -> Path:
    configured = _settings()
    if configured.DATABASE_URL is None:
        raise BackupError("DATABASE_URL must be configured for backups.")
    destination = _validate_backup_destination(destination, configured)

    timestamp = datetime.now(timezone.utc)
    backup_name = timestamp.strftime("work-station-%Y%m%dT%H%M%SZ")
    final_directory = destination / backup_name
    if final_directory.exists():
        raise BackupError("A backup with this timestamp already exists.")
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=".work-station-backup-", dir=destination)
    )
    try:
        database_dump = temporary_directory / "database.dump"
        database_url = make_url(str(configured.DATABASE_URL)).set(
            drivername="postgresql"
        )
        _run_private(
            [
                "pg_dump",
                *_database_arguments(database_url),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(database_dump),
            ],
            _command_environment(database_url, configured),
        )

        components = ["database"]
        checksummed_names = ["database.dump"]
        if configured.ASSET_STORAGE_ROOT is not None:
            _write_asset_archive(
                configured.ASSET_STORAGE_ROOT,
                temporary_directory / "assets.tar.gz",
            )
            components.append("assets")
            checksummed_names.append("assets.tar.gz")

        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": timestamp.isoformat(),
            "application_commit": _git_commit(),
            "components": components,
        }
        (temporary_directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksummed_names.append("manifest.json")
        _write_checksums(temporary_directory, checksummed_names)
        verify_backup(temporary_directory)
        temporary_directory.rename(final_directory)
        return final_directory
    except BaseException:
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)
        raise


def _safe_archive_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise BackupError("The asset archive contains too many entries.")
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "assets"
            or member.issym()
            or member.islnk()
            or member.isdev()
            or not (member.isdir() or member.isfile())
        ):
            raise BackupError("The asset archive contains an unsafe entry.")
    return members


def verify_backup(backup_directory: Path) -> dict[str, object]:
    backup_directory = backup_directory.expanduser().resolve()
    if backup_directory.is_symlink() or not backup_directory.is_dir():
        raise BackupError("The backup path must be a regular directory.")
    required = {"database.dump", "manifest.json", "SHA256SUMS"}
    existing = {item.name for item in backup_directory.iterdir()}
    if not required.issubset(existing):
        raise BackupError("The backup is missing required files.")
    if existing - (required | {"assets.tar.gz"}):
        raise BackupError("The backup contains unexpected files.")

    try:
        manifest = json.loads(
            (backup_directory / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("The backup manifest is invalid.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != FORMAT_VERSION
        or set(manifest) != {
            "format_version",
            "created_at",
            "application_commit",
            "components",
        }
        or manifest.get("components")
        not in (["database"], ["database", "assets"])
    ):
        raise BackupError("The backup manifest is not supported.")

    checksum_lines = (backup_directory / "SHA256SUMS").read_text(
        encoding="ascii"
    ).splitlines()
    expected_names = {"database.dump", "manifest.json"}
    if "assets" in manifest["components"]:
        expected_names.add("assets.tar.gz")
    checksums: dict[str, str] = {}
    for line in checksum_lines:
        digest, separator, name = line.partition("  ")
        if (
            separator != "  "
            or name not in expected_names
            or name in checksums
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise BackupError("The backup checksum file is invalid.")
        checksums[name] = digest
    if set(checksums) != expected_names:
        raise BackupError("The backup checksum set is incomplete.")
    for name, expected_digest in checksums.items():
        if not hmac.compare_digest(_sha256(backup_directory / name), expected_digest):
            raise BackupError("Backup checksum validation failed.")

    _run_private(
        ["pg_restore", "--list", str(backup_directory / "database.dump")],
        os.environ.copy(),
    )
    asset_archive = backup_directory / "assets.tar.gz"
    if asset_archive.exists():
        with tarfile.open(asset_archive, "r:gz") as archive:
            _safe_archive_members(archive)
    return manifest


def _database_identity(url: URL) -> tuple[str | None, int, str | None]:
    return (url.host, url.port or 5432, url.database)


def restore_backup(backup_directory: Path, asset_destination: Path | None) -> None:
    if os.environ.get("WORK_STATION_CONFIRM_DISPOSABLE_RESTORE") != "YES":
        raise BackupError(
            "Set WORK_STATION_CONFIRM_DISPOSABLE_RESTORE=YES for an explicit disposable target."
        )
    target_value = os.environ.get("WORK_STATION_RESTORE_DATABASE_URL")
    if not target_value:
        raise BackupError("WORK_STATION_RESTORE_DATABASE_URL is required.")
    target_url = make_url(target_value).set(drivername="postgresql")
    configured = _settings()
    if configured.DATABASE_URL is not None:
        live_url = make_url(str(configured.DATABASE_URL)).set(drivername="postgresql")
        if _database_identity(target_url) == _database_identity(live_url):
            raise BackupError("Restore refuses the configured application database.")

    manifest = verify_backup(backup_directory)
    _run_private(
        [
            "pg_restore",
            *_database_arguments(target_url),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            str(backup_directory.expanduser().resolve() / "database.dump"),
        ],
        _command_environment(target_url, configured),
    )

    if "assets" in manifest["components"]:
        if asset_destination is None:
            raise BackupError("An empty asset destination is required for this backup.")
        destination = asset_destination.expanduser().resolve()
        if not destination.is_absolute() or not destination.is_dir():
            raise BackupError("The asset destination must be an existing directory.")
        if destination == REPOSITORY_ROOT or REPOSITORY_ROOT in destination.parents:
            raise BackupError("Assets cannot be restored into the source tree.")
        if any(destination.iterdir()):
            raise BackupError("The asset restore destination must be empty.")
        with tarfile.open(
            backup_directory.expanduser().resolve() / "assets.tar.gz", "r:gz"
        ) as archive:
            members = _safe_archive_members(archive)
            archive.extractall(destination, members=members, filter="data")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, verify, or restore a private WORK STATION backup."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("backup_directory", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("backup_directory", type=Path)
    restore.add_argument("--asset-destination", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "backup":
            created = create_backup(arguments.destination)
            print(f"Backup created: {created.name}")
        elif arguments.command == "verify":
            verify_backup(arguments.backup_directory)
            print("Backup verification passed.")
        else:
            restore_backup(arguments.backup_directory, arguments.asset_destination)
            print("Disposable restore completed and validated.")
    except (BackupError, OSError, tarfile.TarError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
