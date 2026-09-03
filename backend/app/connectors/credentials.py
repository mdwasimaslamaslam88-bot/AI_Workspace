from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from app.core.secret_box import KEY_BYTES, SecretBoxError, XChaCha20Poly1305Box


_MAGIC = b"WSCON1\x00"
_ADDITIONAL_DATA = b"work-station-connector-credential-v1"
_MAX_PLAINTEXT_CHARACTERS = 4_096
_MAX_CIPHERTEXT_CHARACTERS = 8_192
_OAUTH2_VERSION = 1


class ConnectorCredentialError(RuntimeError):
    """A connector credential key or ciphertext is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class OAuth2Credential:
    access_token: str
    refresh_token: str | None
    client_id: str | None
    client_secret: str | None
    token_path: str | None
    expires_at: datetime | None


def _validate_secret(value: str | None, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 2_048
        or value != value.strip()
        or any(ord(character) < 0x21 for character in value)
    ):
        raise ValueError("OAuth credential value is invalid")
    return value


def encode_oauth2_credential(value: OAuth2Credential) -> str:
    access_token = _validate_secret(value.access_token, required=True)
    refresh_token = _validate_secret(value.refresh_token, required=False)
    client_secret = _validate_secret(value.client_secret, required=False)
    if value.client_id is not None and (
        not 1 <= len(value.client_id) <= 256
        or value.client_id != value.client_id.strip()
        or any(ord(character) <= 0x20 for character in value.client_id)
    ):
        raise ValueError("OAuth client ID is invalid")
    if value.token_path is not None and (
        not 1 <= len(value.token_path) <= 512
        or value.token_path != value.token_path.strip()
    ):
        raise ValueError("OAuth token path is invalid")
    if value.expires_at is not None:
        if value.expires_at.tzinfo is None:
            raise ValueError("OAuth expiry must be timezone-aware")
        expires_at = value.expires_at.astimezone(timezone.utc).isoformat()
    else:
        expires_at = None
    refresh_fields = (refresh_token, value.client_id, client_secret, value.token_path)
    if any(field is not None for field in refresh_fields) and any(
        field is None for field in refresh_fields
    ):
        raise ValueError("OAuth refresh configuration must be complete")
    payload = {
        "access_token": access_token,
        "client_id": value.client_id,
        "client_secret": client_secret,
        "expires_at": expires_at,
        "refresh_token": refresh_token,
        "token_path": value.token_path,
        "version": _OAUTH2_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded) > _MAX_PLAINTEXT_CHARACTERS:
        raise ValueError("OAuth credential envelope is too large")
    return encoded


def decode_oauth2_credential(value: str) -> OAuth2Credential | None:
    if not value.startswith("{"):
        return None
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ConnectorCredentialError("OAuth credential envelope is invalid") from exc
    expected = {
        "access_token",
        "client_id",
        "client_secret",
        "expires_at",
        "refresh_token",
        "token_path",
        "version",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ConnectorCredentialError("OAuth credential envelope is invalid")
    try:
        if payload["version"] != _OAUTH2_VERSION:
            raise ValueError
        access_token = _validate_secret(payload["access_token"], required=True)
        refresh_token = _validate_secret(payload["refresh_token"], required=False)
        client_secret = _validate_secret(payload["client_secret"], required=False)
        client_id = payload["client_id"]
        token_path = payload["token_path"]
        if client_id is not None and (
            not isinstance(client_id, str)
            or not 1 <= len(client_id) <= 256
            or client_id != client_id.strip()
            or any(ord(character) <= 0x20 for character in client_id)
        ):
            raise ValueError
        if token_path is not None and (
            not isinstance(token_path, str)
            or not 1 <= len(token_path) <= 512
            or token_path != token_path.strip()
        ):
            raise ValueError
        expires_at = (
            datetime.fromisoformat(payload["expires_at"])
            if payload["expires_at"] is not None
            else None
        )
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError
        refresh_fields = (refresh_token, client_id, client_secret, token_path)
        if any(field is not None for field in refresh_fields) and any(
            field is None for field in refresh_fields
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ConnectorCredentialError("OAuth credential envelope is invalid") from exc
    assert access_token is not None
    return OAuth2Credential(
        access_token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_path=token_path,
        expires_at=expires_at,
    )


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
            or not 16 <= len(credential) <= _MAX_PLAINTEXT_CHARACTERS
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
            not 16 <= len(credential) <= _MAX_PLAINTEXT_CHARACTERS
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
