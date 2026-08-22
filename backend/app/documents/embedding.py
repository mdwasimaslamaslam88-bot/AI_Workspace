from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass
from typing import Protocol

from app.models.document import DOCUMENT_EMBEDDING_DIMENSIONS


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}\Z")
_EMBEDDING_FORMAT = f"<{DOCUMENT_EMBEDDING_DIMENSIONS}f"
HASH_EMBEDDING_MODEL_ID = "local-hash-v1"
MAX_EMBEDDING_DIMENSIONS = 4_096


class EmbeddingError(ValueError):
    """Text cannot be represented by the deterministic local embedding."""


@dataclass(frozen=True, slots=True)
class LocalEmbedding:
    packed: bytes
    norm: float
    model_id: str = HASH_EMBEDDING_MODEL_ID
    dimensions: int = DOCUMENT_EMBEDDING_DIMENSIONS


class EmbeddingRuntime(Protocol):
    model_id: str

    async def embed_texts(
        self,
        texts: tuple[str, ...],
    ) -> tuple[LocalEmbedding, ...]: ...


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


def validate_embedding_model_id(model_id: str) -> str:
    if not isinstance(model_id, str) or not _MODEL_ID_PATTERN.fullmatch(model_id):
        raise EmbeddingError("embedding model identity is invalid")
    return model_id


def pack_embedding(values: tuple[float, ...], model_id: str) -> LocalEmbedding:
    validate_embedding_model_id(model_id)
    dimensions = len(values)
    if not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
        raise EmbeddingError("embedding dimensions are invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        raise EmbeddingError("embedding values are invalid")
    numeric = tuple(float(value) for value in values)
    if any(not math.isfinite(value) for value in numeric):
        raise EmbeddingError("embedding values are invalid")
    norm = math.sqrt(sum(value * value for value in numeric))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingError("embedding norm is invalid")
    normalized = tuple(value / norm for value in numeric)
    return LocalEmbedding(
        packed=struct.pack(f"<{dimensions}f", *normalized),
        norm=1.0,
        model_id=model_id,
        dimensions=dimensions,
    )


def cosine_similarity(
    left: bytes,
    right: bytes,
    dimensions: int | None = None,
) -> float:
    if dimensions is None:
        if len(left) != len(right) or len(left) % 4 != 0:
            raise EmbeddingError("stored embedding has invalid dimensions")
        dimensions = len(left) // 4
    if not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
        raise EmbeddingError("stored embedding has invalid dimensions")
    embedding_format = f"<{dimensions}f"
    expected = struct.calcsize(embedding_format)
    if len(left) != expected or len(right) != expected:
        raise EmbeddingError("stored embedding has invalid dimensions")
    left_values = struct.unpack(embedding_format, left)
    right_values = struct.unpack(embedding_format, right)
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left_values, right_values)
    )
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm <= 0 or right_norm <= 0:
        raise EmbeddingError("stored embedding norm is invalid")
    score = dot_product / (left_norm * right_norm)
    if not math.isfinite(score):
        raise EmbeddingError("embedding score is invalid")
    return score
