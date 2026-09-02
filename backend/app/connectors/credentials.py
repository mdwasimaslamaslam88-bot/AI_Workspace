from __future__ import annotations

import base64
import os
from pathlib import Path
import stat

from app.core.secret_box import KEY_BYTES, SecretBoxError, XChaCha20Poly1305Box


_MAGIC = b"WSCON1\x00"
_ADDITIONAL_DATA = b"work-station-connector-credential-v1"
_MAX_CIPHERTEXT_CHARACTERS = 2_048


class ConnectorCredentialError(RuntimeError):
    """A connector credential key or ciphertext is unsafe or invalid."""


class ConnectorCredentialBox:
    """Encrypt connector credentials while keeping the master key outside PostgreSQL."""

    def __init__(self, state_root: Path) -> None:
        root = Path(state_root)
        if not root.is_absolute():
            raise ValueError("connector state root must be absolute")
        if root.exists() and root.is_symlink():
            raise ConnectorCredentialError("connector state root must not be a link")
        self.root = root.resolve(strict=False)
        self.key_path = self.root / "connector-credentials.key"
        try:
            self._box = XChaCha20Poly1305Box(
                magic=_MAGIC,
                additional_data=_ADDITIONAL_DATA,
            )
        except SecretBoxError as exc:
            raise ConnectorCredentialError(str(exc)) from exc
        self._initialize_root()
        self._key = self._load_or_create_key()

    def _initialize_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.root.stat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ConnectorCredentialError("connector state root must be owner-only")

    def _load_or_create_key(self) -> bytes:
        try:
            metadata = self.key_path.lstat()
        except FileNotFoundError:
            key = os.urandom(KEY_BYTES)
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as target:
                target.write(key)
                target.flush()
                os.fsync(target.fileno())
            self._fsync_root()
            return key
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != KEY_BYTES
        ):
            raise ConnectorCredentialError("connector credential key is unsafe")
        key = self.key_path.read_bytes()
        if len(key) != KEY_BYTES:
            raise ConnectorCredentialError("connector credential key is invalid")
        return key

    def encrypt(self, credential: str) -> str:
        if (
            not isinstance(credential, str)
            or not 16 <= len(credential) <= 512
            or credential != credential.strip()
            or any(ord(character) < 0x21 for character in credential)
        ):
            raise ValueError("connector credential is invalid")
        encrypted = self._box.encrypt(credential.encode("utf-8"), self._key)
        encoded = base64.b64encode(encrypted).decode("ascii")
        if len(encoded) > _MAX_CIPHERTEXT_CHARACTERS:
            raise ConnectorCredentialError("connector credential ciphertext is too large")
        return encoded

    def decrypt(self, ciphertext: str) -> str:
        if (
            not isinstance(ciphertext, str)
            or not 1 <= len(ciphertext) <= _MAX_CIPHERTEXT_CHARACTERS
        ):
            raise ConnectorCredentialError("connector credential ciphertext is invalid")
        try:
            payload = base64.b64decode(ciphertext, validate=True)
            credential = self._box.decrypt(payload, self._key).decode("utf-8")
        except (ValueError, UnicodeError, SecretBoxError) as exc:
            raise ConnectorCredentialError(
                "connector credential authentication failed"
            ) from exc
        if (
            not 16 <= len(credential) <= 512
            or credential != credential.strip()
            or any(ord(character) < 0x21 for character in credential)
        ):
            raise ConnectorCredentialError("connector credential plaintext is invalid")
        return credential

    def _fsync_root(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
