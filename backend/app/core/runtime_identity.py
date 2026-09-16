"""Bounded startup evidence for the existing backend and compiled PWA."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess

from pydantic import BaseModel, ConfigDict, Field


_REPORT_ONLY_PATH_PREFIXES = ("reports/ASTER_AI_OS_",)
_MAX_REPORT_ONLY_ANCESTRY = 256


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


def application_source_commit(repository: Path) -> str | None:
    """Resolve the executable source tip, excluding live report-only commits.

    The ASTER controller may append report commits while the backend remains
    the same application.  Runtime identity must therefore use the same
    bounded ancestry rule as the controller instead of exposing the mutable
    checkout ``HEAD``.  A non-report commit stops the walk; malformed or
    excessively long ancestry fails closed.
    """

    def run_git(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                check=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip()

    current = run_git("rev-parse", "HEAD")
    if not current or len(current) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in current
    ):
        return None

    for _ in range(_MAX_REPORT_ONLY_ANCESTRY):
        changed = run_git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", current
        )
        paths = [path.strip() for path in (changed or "").splitlines() if path.strip()]
        if not paths or not all(
            any(path.startswith(prefix) for prefix in _REPORT_ONLY_PATH_PREFIXES)
            for path in paths
        ):
            return current
        parent = run_git("rev-parse", f"{current}^")
        if not parent or len(parent) not in (40, 64) or any(
            character not in "0123456789abcdef" for character in parent
        ):
            return None
        current = parent

    return None


def capture_runtime_identity(repository: Path, web_root: Path | None, version: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        captured_at=datetime.now(timezone.utc),
        version=version,
        source_commit=application_source_commit(repository),
        backend_source_sha256=tree_digest(
            repository / "backend" / "app", suffixes=frozenset({".py", ".json"})
        ),
        web_bundle_sha256=tree_digest(web_root) if web_root is not None else None,
    )
