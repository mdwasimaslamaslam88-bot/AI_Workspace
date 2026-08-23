import zlib

import pytest

from app.images import (
    GeneratedImage,
    ImageRuntimeInputError,
    ImageRuntimeUnavailableError,
    image_dimensions,
    sanitize_generated_png,
)


def _png(width: int, height: int) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + name
            + data
            + (zlib.crc32(name + data) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"bounded fixture"))
        + chunk(b"IEND", b"")
    )


def _jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xe0\x00\x04xx"
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xd9"
    )


def test_image_dimensions_parse_bounded_png_and_jpeg_headers():
    png = image_dimensions(_png(768, 512), "image/png")
    jpeg = image_dimensions(_jpeg(640, 512), "image/jpeg")
    assert (png.width, png.height) == (768, 512)
    assert (jpeg.width, jpeg.height) == (640, 512)


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"", "image/png"),
        (b"not-png", "image/png"),
        (_png(4_096, 512), "image/png"),
        (_jpeg(512, 4_096), "image/jpeg"),
        (_png(512, 512), "image/gif"),
    ],
)
def test_image_dimensions_reject_malformed_unbounded_or_unsupported_input(
    content, media_type
):
    with pytest.raises(ImageRuntimeInputError):
        image_dimensions(content, media_type)


def test_generated_image_requires_exact_bounded_png_dimensions():
    result = GeneratedImage(_png(512, 512), 512, 512)

    assert result.width == 512
    assert result.height == 512

    with pytest.raises(ImageRuntimeUnavailableError):
        GeneratedImage(_png(512, 512), 768, 512)


def test_generated_png_sanitizer_removes_textual_runtime_metadata():
    content = _png(512, 512)
    insertion = content.rfind(b"\x00\x00\x00\x00IEND")
    name = b"tEXt"
    data = b"prompt\x00private prompt and checkpoint metadata"
    metadata = (
        len(data).to_bytes(4, "big")
        + name
        + data
        + (zlib.crc32(name + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )
    with_metadata = content[:insertion] + metadata + content[insertion:]

    sanitized = sanitize_generated_png(with_metadata)

    assert b"private prompt" not in sanitized
    assert image_dimensions(sanitized, "image/png").width == 512
