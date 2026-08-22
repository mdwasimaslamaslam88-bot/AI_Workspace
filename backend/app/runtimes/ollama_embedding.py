from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import (
    MAX_OLLAMA_EMBEDDING_REQUEST_BYTES,
    MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES,
)
from app.documents.embedding import (
    EmbeddingError,
    LocalEmbedding,
    pack_embedding,
    validate_embedding_model_id,
)


class OllamaEmbeddingUnavailableError(RuntimeError):
    """The bounded local embedding runtime could not complete safely."""


class OllamaEmbeddingRuntime:
    def __init__(
        self,
        client: httpx.AsyncClient,
        model_reference: str,
        *,
        timeout_seconds: float = 60.0,
        max_request_bytes: int = 1_048_576,
        max_response_bytes: int = 1_048_576,
        batch_size: int = 16,
        max_active: int = 1,
        keep_alive_seconds: int = 0,
    ) -> None:
        if not model_reference or model_reference != model_reference.strip():
            raise ValueError("embedding model reference must be exact and nonblank")
        if len(model_reference) > 240:
            raise ValueError("embedding model reference is too long")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 300
        ):
            raise ValueError("embedding timeout is outside its bound")
        for name, value, maximum in (
            ("request", max_request_bytes, MAX_OLLAMA_EMBEDDING_REQUEST_BYTES),
            (
                "response",
                max_response_bytes,
                MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES,
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"embedding {name} cap must be an integer")
            if not 1 <= value <= maximum:
                raise ValueError(f"embedding {name} cap is outside its bound")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("embedding batch size must be an integer")
        if not 1 <= batch_size <= 64:
            raise ValueError("embedding batch size is outside its bound")
        if isinstance(max_active, bool) or not isinstance(max_active, int):
            raise TypeError("embedding concurrency must be an integer")
        if not 1 <= max_active <= 4:
            raise ValueError("embedding concurrency is outside its bound")
        if isinstance(keep_alive_seconds, bool) or not isinstance(
            keep_alive_seconds, int
        ):
            raise TypeError("embedding keep-alive must be an integer")
        if not 0 <= keep_alive_seconds <= 3_600:
            raise ValueError("embedding keep-alive is outside its bound")

        self.client = client
        self.model_reference = model_reference
        self.model_id = validate_embedding_model_id(f"ollama:{model_reference}")
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.batch_size = batch_size
        self.keep_alive_seconds = keep_alive_seconds
        self._admission = asyncio.Semaphore(max_active)

    async def embed_texts(
        self,
        texts: tuple[str, ...],
    ) -> tuple[LocalEmbedding, ...]:
        if not isinstance(texts, tuple):
            raise TypeError("embedding inputs must be a tuple")
        if not texts:
            return ()
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("embedding inputs must be nonblank text")

        results: list[LocalEmbedding] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            results.extend(await self._embed_batch(batch))
        return tuple(results)

    async def _embed_batch(
        self,
        texts: tuple[str, ...],
    ) -> tuple[LocalEmbedding, ...]:
        payload: Mapping[str, Any] = {
            "model": self.model_reference,
            "input": list(texts),
            "truncate": False,
            "keep_alive": self.keep_alive_seconds,
        }
        try:
            request_body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except Exception as exc:
            raise OllamaEmbeddingUnavailableError(
                "local embedding is unavailable"
            ) from exc
        if len(request_body) > self.max_request_bytes:
            request_body = b""
            raise OllamaEmbeddingUnavailableError("local embedding is unavailable")

        response_body = bytearray()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self._admission.acquire()
                try:
                    async with self.client.stream(
                        "POST",
                        "/api/embed",
                        headers={
                            "Accept-Encoding": "identity",
                            "Content-Type": "application/json",
                        },
                        content=request_body,
                        timeout=self.timeout_seconds,
                    ) as response:
                        response.raise_for_status()
                        if (
                            response.headers.get(
                                "content-encoding", "identity"
                            ).lower()
                            != "identity"
                        ):
                            raise OllamaEmbeddingUnavailableError(
                                "local embedding is unavailable"
                            )
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            if (
                                not declared.isdigit()
                                or int(declared) > self.max_response_bytes
                            ):
                                raise OllamaEmbeddingUnavailableError(
                                    "local embedding is unavailable"
                                )
                        async for chunk in response.aiter_bytes():
                            if len(chunk) > self.max_response_bytes - len(response_body):
                                raise OllamaEmbeddingUnavailableError(
                                    "local embedding is unavailable"
                                )
                            response_body.extend(chunk)
                finally:
                    self._admission.release()
        except asyncio.CancelledError:
            raise
        except OllamaEmbeddingUnavailableError:
            raise
        except Exception as exc:
            raise OllamaEmbeddingUnavailableError(
                "local embedding is unavailable"
            ) from exc
        finally:
            request_body = b""

        try:
            decoded = json.loads(response_body)
            if not isinstance(decoded, dict) or "embeddings" not in decoded:
                raise ValueError("embedding response is malformed")
            if decoded.get("model") != self.model_reference:
                raise ValueError("embedding response model identity is invalid")
            raw_embeddings = decoded.get("embeddings")
            if (
                not isinstance(raw_embeddings, list)
                or len(raw_embeddings) != len(texts)
            ):
                raise ValueError("embedding response count is invalid")
            packed = tuple(
                pack_embedding(tuple(values), self.model_id)
                if isinstance(values, list)
                else (_raise_malformed())
                for values in raw_embeddings
            )
            dimensions = {item.dimensions for item in packed}
            if len(dimensions) != 1:
                raise ValueError("embedding dimensions changed within a batch")
            return packed
        except (EmbeddingError, TypeError, ValueError) as exc:
            raise OllamaEmbeddingUnavailableError(
                "local embedding is unavailable"
            ) from exc
        finally:
            response_body.clear()


def _raise_malformed() -> LocalEmbedding:
    raise ValueError("embedding response vector is malformed")
