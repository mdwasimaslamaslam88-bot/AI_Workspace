from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass

from app.models.document import DOCUMENT_EMBEDDING_DIMENSIONS


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_EMBEDDING_FORMAT = f"<{DOCUMENT_EMBEDDING_DIMENSIONS}f"


class EmbeddingError(ValueError):
    """Text cannot be represented by the deterministic local embedding."""


@dataclass(frozen=True, slots=True)
class LocalEmbedding:
    packed: bytes
    norm: float


def _features(text: str) -> tuple[str, ...]:
    tokens = tuple(token.casefold() for token in _TOKEN_PATTERN.findall(text))
    if not tokens:
        raise EmbeddingError("text contains no embedding features")
    bigrams = tuple(
        f"{left}\x1f{right}" for left, right in zip(tokens, tokens[1:])
    )
    return tokens + bigrams


def embed_text(text: str) -> LocalEmbedding:
    if not isinstance(text, str):
        raise TypeError("embedding input must be text")
    values = [0.0] * DOCUMENT_EMBEDDING_DIMENSIONS
    for feature in _features(text):
        digest = hashlib.blake2b(
            feature.encode("utf-8"),
            digest_size=8,
            person=b"personal-ai-rag",
        ).digest()
        index = int.from_bytes(digest[:4], "little") % DOCUMENT_EMBEDDING_DIMENSIONS
        sign = -1.0 if digest[4] & 1 else 1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingError("embedding norm is invalid")
    normalized = tuple(value / norm for value in values)
    return LocalEmbedding(
        packed=struct.pack(_EMBEDDING_FORMAT, *normalized),
        norm=1.0,
    )


def cosine_similarity(left: bytes, right: bytes) -> float:
    expected = struct.calcsize(_EMBEDDING_FORMAT)
    if len(left) != expected or len(right) != expected:
        raise EmbeddingError("stored embedding has invalid dimensions")
    left_values = struct.unpack(_EMBEDDING_FORMAT, left)
    right_values = struct.unpack(_EMBEDDING_FORMAT, right)
    score = sum(
        left_value * right_value
        for left_value, right_value in zip(left_values, right_values)
    )
    if not math.isfinite(score):
        raise EmbeddingError("embedding score is invalid")
    return score
