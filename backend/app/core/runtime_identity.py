"""Bounded startup evidence for the existing backend and compiled PWA."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess

from pydantic import BaseModel, ConfigDict, Field


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    captured_at: datetime
    version: str
    source_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40,64}$")
    backend_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    web_bundle_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def tree_digest(root: Path, *, suffixes: frozenset[str] | None = None) -> str | None:
    """Hash relative paths and file digests; never follow source symlinks."""
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        paths = []
        for path in root.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                return None
            if path.is_file() and (suffixes is None or path.suffix in suffixes):
                paths.append(path)
                if len(paths) > 4096:
                    return None
        if not paths:
            return None
        result = hashlib.sha256()
        total = 0
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
            content_digest = hashlib.sha256()
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
                while chunk := source.read(65_536):
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        return None
                    content_digest.update(chunk)
            result.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            result.update(content_digest.digest())
        return result.hexdigest()
    except (OSError, RuntimeError):
        return None


def capture_runtime_identity(repository: Path, web_root: Path | None, version: str) -> RuntimeIdentity:
    commit = None
    try:
        captured = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=2,
        ).stdout.strip()
        if len(captured) in (40, 64) and all(c in "0123456789abcdef" for c in captured):
            commit = captured
    except (OSError, subprocess.SubprocessError):
        pass
    return RuntimeIdentity(
        captured_at=datetime.now(timezone.utc),
        version=version,
        source_commit=commit,
        backend_source_sha256=tree_digest(
            repository / "backend" / "app", suffixes=frozenset({".py", ".json"})
        ),
        web_bundle_sha256=tree_digest(web_root) if web_root is not None else None,
    )
