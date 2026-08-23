from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import zlib


MAX_IMAGE_INPUT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 1_048_576
MIN_IMAGE_DIMENSION = 64
MAX_IMAGE_DIMENSION = 2_048
_JPEG_START_OF_FRAME_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


class ImageRuntimeUnavailableError(RuntimeError):
    """A configured local image runtime failed without exposing details."""


class ImageRuntimeInputError(ValueError):
    """An image request exceeded a fixed validation or resource bound."""


@dataclass(frozen=True, slots=True)
class ImageDimensions:
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.width, bool)
            or not isinstance(self.width, int)
            or isinstance(self.height, bool)
            or not isinstance(self.height, int)
            or not MIN_IMAGE_DIMENSION <= self.width <= MAX_IMAGE_DIMENSION
            or not MIN_IMAGE_DIMENSION <= self.height <= MAX_IMAGE_DIMENSION
            or self.width * self.height > MAX_IMAGE_PIXELS
        ):
            raise ImageRuntimeInputError("image dimensions are outside their bound")


def _png_dimensions(content: bytes) -> ImageDimensions:
    if len(content) < 45 or content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageRuntimeInputError("PNG input is malformed")
    position = 8
    dimensions: ImageDimensions | None = None
    seen_idat = False
    chunk_index = 0
    while position + 12 <= len(content):
        length = int.from_bytes(content[position : position + 4], "big")
        chunk_type = content[position + 4 : position + 8]
        chunk_end = position + 12 + length
        if chunk_end > len(content):
            raise ImageRuntimeInputError("PNG input is malformed")
        data = content[position + 8 : position + 8 + length]
        expected_crc = int.from_bytes(content[position + 8 + length : chunk_end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise ImageRuntimeInputError("PNG input is malformed")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise ImageRuntimeInputError("PNG input is malformed")
            dimensions = ImageDimensions(
                int.from_bytes(data[:4], "big"),
                int.from_bytes(data[4:8], "big"),
            )
        elif chunk_type == b"IHDR":
            raise ImageRuntimeInputError("PNG input is malformed")
        if chunk_type == b"IDAT":
            seen_idat = True
        if chunk_type == b"IEND":
            if length != 0 or chunk_end != len(content) or not seen_idat:
                raise ImageRuntimeInputError("PNG input is malformed")
            if dimensions is None:  # pragma: no cover - first-chunk guard
                raise ImageRuntimeInputError("PNG input is malformed")
            return dimensions
        position = chunk_end
        chunk_index += 1
    raise ImageRuntimeInputError("PNG input is malformed")


def _jpeg_dimensions(content: bytes) -> ImageDimensions:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        raise ImageRuntimeInputError("JPEG input is malformed")
    position = 2
    while position < len(content):
        while position < len(content) and content[position] == 0xFF:
            position += 1
        if position >= len(content):
            break
        marker = content[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if marker in {0xD9, 0xDA} or position + 2 > len(content):
            break
        segment_length = int.from_bytes(content[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(content):
            raise ImageRuntimeInputError("JPEG input is malformed")
        if marker in _JPEG_START_OF_FRAME_MARKERS:
            if segment_length < 7:
                raise ImageRuntimeInputError("JPEG input is malformed")
            return ImageDimensions(
                int.from_bytes(content[position + 5 : position + 7], "big"),
                int.from_bytes(content[position + 3 : position + 5], "big"),
            )
        position += segment_length
    raise ImageRuntimeInputError("JPEG dimensions are unavailable")


def image_dimensions(content: bytes, media_type: str) -> ImageDimensions:
    if not isinstance(content, bytes):
        raise TypeError("image content must be bytes")
    if not 0 < len(content) <= MAX_IMAGE_INPUT_BYTES:
        raise ImageRuntimeInputError("image input is outside its byte bound")
    if media_type == "image/png":
        return _png_dimensions(content)
    if media_type == "image/jpeg":
        return _jpeg_dimensions(content)
    raise ImageRuntimeInputError("image input type is unsupported")


def sanitize_generated_png(content: bytes) -> bytes:
    if not isinstance(content, bytes):
        raise TypeError("generated PNG content must be bytes")
    if not 0 < len(content) <= MAX_IMAGE_OUTPUT_BYTES:
        raise ImageRuntimeUnavailableError("generated image exceeded its bound")
    try:
        _png_dimensions(content)
    except ImageRuntimeInputError as exc:
        raise ImageRuntimeUnavailableError(
            "generated image output is invalid"
        ) from exc
    sanitized = bytearray(content[:8])
    position = 8
    while position < len(content):
        length = int.from_bytes(content[position : position + 4], "big")
        chunk_end = position + 12 + length
        chunk_type = content[position + 4 : position + 8]
        if chunk_type in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
            sanitized.extend(content[position:chunk_end])
        position = chunk_end
    if len(sanitized) > MAX_IMAGE_OUTPUT_BYTES:
        raise ImageRuntimeUnavailableError("generated image exceeded its bound")
    return bytes(sanitized)


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise TypeError("generated image content must be bytes")
        if not 0 < len(self.content) <= MAX_IMAGE_OUTPUT_BYTES:
            raise ImageRuntimeUnavailableError("generated image exceeded its bound")
        try:
            actual = _png_dimensions(self.content)
        except ImageRuntimeInputError as exc:
            raise ImageRuntimeUnavailableError(
                "generated image output is invalid"
            ) from exc
        if (actual.width, actual.height) != (self.width, self.height):
            raise ImageRuntimeUnavailableError(
                "generated image dimensions changed unexpectedly"
            )


class ImageGenerationRuntime(Protocol):
    runtime_id: str
    model_reference: str

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str,
        width: int,
        height: int,
        steps: int,
        guidance: float,
        seed: int,
    ) -> GeneratedImage: ...


class ImageEditingRuntime(Protocol):
    runtime_id: str
    model_reference: str

    async def edit(
        self,
        source: bytes,
        source_media_type: str,
        instruction: str,
        *,
        negative_prompt: str,
        mask: bytes | None,
        mask_media_type: str | None,
        steps: int,
        guidance: float,
        denoise: float,
        seed: int,
    ) -> GeneratedImage: ...
