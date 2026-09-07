from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import BoundedSemaphore, Event, Lock
import time
from typing import Any
from uuid import UUID, uuid4


MAX_FILESYSTEM_PATH_CHARACTERS = 512
MAX_FILESYSTEM_WRITE_BYTES = 65_536
MAX_FILESYSTEM_READ_BYTES = 65_536
MAX_FILESYSTEM_LIST_ENTRIES = 200
MAX_FILESYSTEM_SCAN_ENTRIES = 4096
MAX_PENDING_FILESYSTEM_WRITES = 4
FILESYSTEM_WRITE_SECONDS = 5.0


class FilesystemToolError(RuntimeError):
    """A filesystem request failed a bounded workspace policy."""


class FilesystemToolVerificationError(FilesystemToolError):
    """A filesystem state did not match the independently read result."""


class FilesystemWriteInterrupted(FilesystemToolError):
    """Cancellation or a deadline prevented a filesystem mutation."""


@dataclass(slots=True)
class FilesystemWriteControl:
    deadline: float
    cancelled: Event = field(default_factory=Event)
    published: bool = False

    def check(self) -> None:
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise FilesystemWriteInterrupted("filesystem write was interrupted")


_SENSITIVE_PATH_PARTS = frozenset(
    {
        ".env",
        ".git",
        ".gnupg",
        ".ssh",
        "credential",
        "credentials",
        "private_key",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
        r"\s*[:=]\s*[^\s]{8,}"
    ),
)


@dataclass(frozen=True, slots=True)
class FilesystemOperationResult:
    operation: str
    path: str
    payload: dict[str, Any]


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


class OwnerFilesystemWorkspace:
    """Owner-isolated, relative-path-only access beneath explicit roots."""

    def __init__(self, roots: tuple[Path, ...]) -> None:
        validated: list[Path] = []
        for root in roots:
            if not isinstance(root, Path) or not root.is_absolute():
                raise ValueError("filesystem tool roots must be absolute paths")
            if root.is_symlink():
                raise ValueError("filesystem tool roots must not be symlinks")
            resolved = root.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError("filesystem tool roots must be directories")
            if resolved in validated:
                raise ValueError("filesystem tool roots must be unique")
            validated.append(resolved)
        if len(validated) > 4:
            raise ValueError("at most four filesystem tool roots may be configured")
        self._roots = tuple(validated)
        self._write_lock = Lock()
        # Capacity belongs to the existing workspace and is held until the
        # actual thread exits, including when its caller has gone away.
        self._write_slots = BoundedSemaphore(MAX_PENDING_FILESYSTEM_WRITES)

    def reserve_write(self) -> bool:
        return self._write_slots.acquire(blocking=False)

    def release_write(self) -> None:
        self._write_slots.release()

    @contextmanager
    def _write_admission(self, control: FilesystemWriteControl):
        while True:
            control.check()
            remaining = max(0.0, control.deadline - time.monotonic())
            if self._write_lock.acquire(timeout=min(0.05, remaining)):
                break
        try:
            control.check()
            yield
        finally:
            self._write_lock.release()

    @property
    def available(self) -> bool:
        return bool(self._roots)

    @property
    def root_count(self) -> int:
        return len(self._roots)

    def _owner_root(self, owner_id: UUID, root_index: int) -> Path:
        if isinstance(root_index, bool) or not isinstance(root_index, int):
            raise FilesystemToolError("filesystem root selector is invalid")
        if not 0 <= root_index < len(self._roots):
            raise FilesystemToolError("filesystem root is unavailable")
        root = self._roots[root_index]
        owner_root = root / str(owner_id)
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise FilesystemToolError("safe filesystem directory access is unavailable")
        try:
            owner_root.mkdir(mode=0o700, exist_ok=True)
            if owner_root.is_symlink() or owner_root.resolve(strict=True).parent != root:
                raise FilesystemToolError("filesystem owner workspace is unsafe")
            owner_fd = os.open(owner_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                # Apply permissions to the checked directory, even if its path changes.
                os.fchmod(owner_fd, 0o700)
            finally:
                os.close(owner_fd)
        except OSError as exc:
            raise FilesystemToolError("filesystem owner workspace is unavailable") from exc
        return owner_root

    @staticmethod
    def _relative_path(path: str) -> PurePosixPath:
        if not isinstance(path, str) or not 1 <= len(path) <= MAX_FILESYSTEM_PATH_CHARACTERS:
            raise FilesystemToolError("filesystem path is invalid")
        if "\x00" in path or "\\" in path or path != path.strip():
            raise FilesystemToolError("filesystem path is invalid")
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or path.startswith("~"):
            raise FilesystemToolError("filesystem paths must be relative")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise FilesystemToolError("filesystem path traversal is denied")
        lowered = tuple(part.casefold() for part in candidate.parts)
        if any(
            part.startswith(".") or part in _SENSITIVE_PATH_PARTS
            for part in lowered
        ):
            raise FilesystemToolError("protected filesystem paths are denied")
        if Path(candidate.name).suffix.casefold() in _SENSITIVE_SUFFIXES:
            raise FilesystemToolError("protected filesystem paths are denied")
        return candidate

    @staticmethod
    def _walk_parent(owner_root: Path, relative: PurePosixPath) -> tuple[int, str]:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        current_fd = os.open(owner_root, flags)
        try:
            for part in relative.parts[:-1]:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd, relative.name
        except BaseException:
            os.close(current_fd)
            raise

    @staticmethod
    def _stat_at(parent_fd: int, name: str) -> os.stat_result | None:
        try:
            details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(details.st_mode):
            raise FilesystemToolError("filesystem symlinks are denied")
        if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
            raise FilesystemToolError("filesystem hardlinks are denied")
        return details

    def write(
        self,
        owner_id: UUID,
        path: str,
        content: str,
        *,
        root_index: int = 0,
        control: FilesystemWriteControl | None = None,
    ) -> FilesystemOperationResult:
        control = control or FilesystemWriteControl(time.monotonic() + FILESYSTEM_WRITE_SECONDS)
        control.check()
        if not isinstance(content, str):
            raise FilesystemToolError("filesystem content is invalid")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILESYSTEM_WRITE_BYTES:
            raise FilesystemToolError("filesystem content exceeded its bound")
        if _contains_secret(content):
            raise FilesystemToolError("credential-like content is denied")
        relative = self._relative_path(path)
        digest = hashlib.sha256(encoded).hexdigest()
        with self._write_admission(control):
            owner_root = self._owner_root(owner_id, root_index)
            control.check()
            try:
                parent_fd, name = self._walk_parent(owner_root, relative)
            except OSError as exc:
                raise FilesystemToolError("filesystem parent is unavailable") from exc
            temporary_name: str | None = None
            try:
                existing = self._stat_at(parent_fd, name)
                if existing is not None:
                    if not stat.S_ISREG(existing.st_mode):
                        raise FilesystemToolError("filesystem target is not a regular file")
                    existing_content = self._read_at(parent_fd, name, MAX_FILESYSTEM_READ_BYTES)
                    if existing_content == encoded:
                        return FilesystemOperationResult(
                            "write",
                            relative.as_posix(),
                            {
                                "bytes_written": len(encoded),
                                "changed": False,
                                "sha256": digest,
                            },
                        )
                    raise FilesystemToolError("filesystem target already exists")

                control.check()
                temporary_name = f".ai-os-{uuid4().hex}.tmp"
                open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    open_flags |= os.O_NOFOLLOW
                file_fd = os.open(
                    temporary_name,
                    open_flags,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    view = memoryview(encoded)
                    while view:
                        control.check()
                        written = os.write(file_fd, view)
                        if written <= 0:
                            raise OSError("filesystem write made no progress")
                        view = view[written:]
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                control.check()
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                # After publication, finish cleanup and verification even when
                # cancellation arrives. Never erase a completed effect.
                control.published = True
                os.unlink(temporary_name, dir_fd=parent_fd)
                temporary_name = None
                if self._read_at(parent_fd, name, MAX_FILESYSTEM_READ_BYTES) != encoded:
                    raise FilesystemToolVerificationError("filesystem write verification failed")
                return FilesystemOperationResult(
                    "write",
                    relative.as_posix(),
                    {
                        "bytes_written": len(encoded),
                        "changed": True,
                        "sha256": digest,
                    },
                )
            except FilesystemToolError:
                raise
            except OSError as exc:
                raise FilesystemToolError("filesystem write failed safely") from exc
            finally:
                if temporary_name is not None:
                    try:
                        os.unlink(temporary_name, dir_fd=parent_fd)
                    except OSError:
                        pass
                os.close(parent_fd)

    @staticmethod
    def _read_at(parent_fd: int, name: str, maximum: int) -> bytes:
        # A FIFO/device must reach the regular-file check without blocking
        # open(), including when an entry is replaced immediately before open.
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            details = os.fstat(file_fd)
            # Defense in depth against detectable preseeded aliases. A single
            # remaining link cannot establish an inode's historical provenance.
            if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
                raise FilesystemToolError("filesystem hardlinks are denied")
            if not stat.S_ISREG(details.st_mode) or details.st_size > maximum:
                raise FilesystemToolError("filesystem file exceeded its read bound")
            content = bytearray()
            while len(content) <= maximum:
                chunk = os.read(file_fd, min(16_384, maximum + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > maximum:
                raise FilesystemToolError("filesystem file exceeded its read bound")
            return bytes(content)
        finally:
            os.close(file_fd)

    def read(
        self,
        owner_id: UUID,
        path: str,
        *,
        root_index: int = 0,
        max_bytes: int = MAX_FILESYSTEM_READ_BYTES,
    ) -> FilesystemOperationResult:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise FilesystemToolError("filesystem read bound is invalid")
        if not 1 <= max_bytes <= MAX_FILESYSTEM_READ_BYTES:
            raise FilesystemToolError("filesystem read bound is invalid")
        relative = self._relative_path(path)
        owner_root = self._owner_root(owner_id, root_index)
        try:
            parent_fd, name = self._walk_parent(owner_root, relative)
            try:
                details = self._stat_at(parent_fd, name)
                if details is None or not stat.S_ISREG(details.st_mode):
                    raise FilesystemToolError("filesystem file is unavailable")
                encoded = self._read_at(parent_fd, name, max_bytes)
            finally:
                os.close(parent_fd)
        except FilesystemToolError:
            raise
        except OSError as exc:
            raise FilesystemToolError("filesystem read failed safely") from exc
        try:
            content = encoded.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise FilesystemToolError("filesystem file is not UTF-8 text") from exc
        if _contains_secret(content):
            raise FilesystemToolError("credential-like content is denied")
        return FilesystemOperationResult(
            "read",
            relative.as_posix(),
            {
                "bytes_read": len(encoded),
                "content": content,
                "sha256": hashlib.sha256(encoded).hexdigest(),
            },
        )

    def exists(
        self,
        owner_id: UUID,
        path: str,
        *,
        root_index: int = 0,
    ) -> FilesystemOperationResult:
        relative = self._relative_path(path)
        owner_root = self._owner_root(owner_id, root_index)
        try:
            parent_fd, name = self._walk_parent(owner_root, relative)
            try:
                details = self._stat_at(parent_fd, name)
            finally:
                os.close(parent_fd)
        except FileNotFoundError:
            details = None
        except OSError as exc:
            raise FilesystemToolError("filesystem existence check failed safely") from exc
        return FilesystemOperationResult(
            "exists",
            relative.as_posix(),
            {"exists": details is not None},
        )

    def stat(
        self,
        owner_id: UUID,
        path: str,
        *,
        root_index: int = 0,
    ) -> FilesystemOperationResult:
        relative = self._relative_path(path)
        owner_root = self._owner_root(owner_id, root_index)
        try:
            parent_fd, name = self._walk_parent(owner_root, relative)
            try:
                details = self._stat_at(parent_fd, name)
            finally:
                os.close(parent_fd)
        except OSError as exc:
            raise FilesystemToolError("filesystem metadata check failed safely") from exc
        if details is None:
            raise FilesystemToolError("filesystem entry is unavailable")
        kind = "file" if stat.S_ISREG(details.st_mode) else "directory"
        if kind == "directory" and not stat.S_ISDIR(details.st_mode):
            raise FilesystemToolError("filesystem entry type is denied")
        return FilesystemOperationResult(
            "stat",
            relative.as_posix(),
            {
                "kind": kind,
                "size": details.st_size,
                "modified_ns": details.st_mtime_ns,
            },
        )

    def list(
        self,
        owner_id: UUID,
        path: str = ".",
        *,
        root_index: int = 0,
        limit: int = 100,
    ) -> FilesystemOperationResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise FilesystemToolError("filesystem list bound is invalid")
        if not 1 <= limit <= MAX_FILESYSTEM_LIST_ENTRIES:
            raise FilesystemToolError("filesystem list bound is invalid")
        owner_root = self._owner_root(owner_id, root_index)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if path == ".":
            try:
                directory_fd = os.open(owner_root, flags)
            except OSError as exc:
                raise FilesystemToolError(
                    "filesystem directory is unavailable"
                ) from exc
            display_path = "."
        else:
            relative = self._relative_path(path)
            try:
                parent_fd, name = self._walk_parent(owner_root, relative)
                try:
                    directory_fd = os.open(name, flags, dir_fd=parent_fd)
                finally:
                    os.close(parent_fd)
            except OSError as exc:
                raise FilesystemToolError("filesystem directory is unavailable") from exc
            display_path = relative.as_posix()
        entries: list[dict[str, Any]] = []
        try:
            with os.scandir(directory_fd) as children:
                bounded = []
                for item in children:
                    if len(bounded) >= MAX_FILESYSTEM_SCAN_ENTRIES:
                        raise FilesystemToolError("filesystem directory exceeded its scan bound")
                    bounded.append(item)
            for item in sorted(bounded, key=lambda item: item.name.casefold()):
                if item.is_symlink():
                    continue
                lowered_name = item.name.casefold()
                if (
                    lowered_name.startswith(".")
                    or lowered_name in _SENSITIVE_PATH_PARTS
                    or Path(lowered_name).suffix in _SENSITIVE_SUFFIXES
                ):
                    continue
                details = item.stat(follow_symlinks=False)
                if not (stat.S_ISREG(details.st_mode) or stat.S_ISDIR(details.st_mode)):
                    continue
                if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "kind": "directory" if stat.S_ISDIR(details.st_mode) else "file",
                        "size": details.st_size,
                    }
                )
        except OSError as exc:
            raise FilesystemToolError("filesystem listing failed safely") from exc
        finally:
            os.close(directory_fd)
        return FilesystemOperationResult(
            "list",
            display_path,
            {"entries": entries[:limit], "truncated": len(entries) > limit},
        )
