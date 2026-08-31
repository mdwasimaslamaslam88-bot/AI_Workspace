from __future__ import annotations

import ctypes
import ctypes.util
import os


KEY_BYTES = 32
NONCE_BYTES = 24
TAG_BYTES = 16


class SecretBoxError(RuntimeError):
    pass


class XChaCha20Poly1305Box:
    """Small libsodium binding for authenticated local state encryption."""

    def __init__(self, *, magic: bytes, additional_data: bytes) -> None:
        if not isinstance(magic, bytes) or not 4 <= len(magic) <= 32:
            raise ValueError("secret box magic is invalid")
        if not isinstance(additional_data, bytes) or not additional_data:
            raise ValueError("secret box additional data is invalid")
        library_name = ctypes.util.find_library("sodium")
        if library_name is None:
            raise SecretBoxError("libsodium is unavailable")
        try:
            self._library = ctypes.CDLL(library_name)
        except OSError as exc:
            raise SecretBoxError("libsodium is unavailable") from exc
        if self._library.sodium_init() < 0:
            raise SecretBoxError("libsodium initialization failed")
        byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
        self._library.crypto_aead_xchacha20poly1305_ietf_encrypt.argtypes = (
            byte_pointer,
            ctypes.POINTER(ctypes.c_ulonglong),
            byte_pointer,
            ctypes.c_ulonglong,
            byte_pointer,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            byte_pointer,
            byte_pointer,
        )
        self._library.crypto_aead_xchacha20poly1305_ietf_decrypt.argtypes = (
            byte_pointer,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            byte_pointer,
            ctypes.c_ulonglong,
            byte_pointer,
            ctypes.c_ulonglong,
            byte_pointer,
            byte_pointer,
        )
        self.magic = magic
        self.additional_data = additional_data

    @staticmethod
    def _buffer(value: bytes):
        return (ctypes.c_ubyte * len(value)).from_buffer_copy(value)

    def encrypt(self, plaintext: bytes, key: bytes) -> bytes:
        self._validate_key(key)
        if not isinstance(plaintext, bytes):
            raise TypeError("secret box plaintext must be bytes")
        nonce = os.urandom(NONCE_BYTES)
        output = (ctypes.c_ubyte * (len(plaintext) + TAG_BYTES))()
        output_length = ctypes.c_ulonglong()
        result = self._library.crypto_aead_xchacha20poly1305_ietf_encrypt(
            output,
            ctypes.byref(output_length),
            self._buffer(plaintext),
            len(plaintext),
            self._buffer(self.additional_data),
            len(self.additional_data),
            None,
            self._buffer(nonce),
            self._buffer(key),
        )
        if result != 0:
            raise SecretBoxError("secret box encryption failed")
        return self.magic + nonce + bytes(output[: output_length.value])

    def decrypt(self, payload: bytes, key: bytes) -> bytes:
        self._validate_key(key)
        if (
            not isinstance(payload, bytes)
            or not payload.startswith(self.magic)
            or len(payload) < len(self.magic) + NONCE_BYTES + TAG_BYTES
        ):
            raise SecretBoxError("secret box format is invalid")
        nonce_start = len(self.magic)
        nonce = payload[nonce_start : nonce_start + NONCE_BYTES]
        ciphertext = payload[nonce_start + NONCE_BYTES :]
        output = (ctypes.c_ubyte * max(1, len(ciphertext)))()
        output_length = ctypes.c_ulonglong()
        result = self._library.crypto_aead_xchacha20poly1305_ietf_decrypt(
            output,
            ctypes.byref(output_length),
            None,
            self._buffer(ciphertext),
            len(ciphertext),
            self._buffer(self.additional_data),
            len(self.additional_data),
            self._buffer(nonce),
            self._buffer(key),
        )
        if result != 0:
            raise SecretBoxError("secret box authentication failed")
        return bytes(output[: output_length.value])

    @staticmethod
    def _validate_key(key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise ValueError("secret box key is invalid")
