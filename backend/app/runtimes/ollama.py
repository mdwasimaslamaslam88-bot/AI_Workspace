from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
import json
import math
import re
from typing import Any

import httpx

from app.ai.catalog import (
    ModelCapability,
    ModelRuntimeUnavailableError,
    ModelScaleClass,
    RuntimeModel,
)
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationRequestTooLargeError,
    TextGenerationResult,
    TextGenerationRole,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)
from app.core.config import (
    MAX_OLLAMA_CATALOG_LIST_MODELS,
    MAX_OLLAMA_CATALOG_RESPONSE_BYTES,
    MAX_OLLAMA_GENERATION_REQUEST_BYTES,
    MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
)


_SAFE_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,254}$")
QWEN3_REASONING_HEADROOM_TOKENS = 768
MAX_QWEN3_PREDICT_TOKENS = 1_792


def _supports_hidden_thinking(runtime_reference: str) -> bool:
    model_name = runtime_reference.rsplit("/", 1)[-1].casefold()
    return model_name == "qwen3" or model_name.startswith("qwen3:")


def _reasoning_exhausted(payload: Any) -> bool:
    if not isinstance(payload, Mapping) or payload.get("done") is not True:
        return False
    message = payload.get("message")
    return (
        isinstance(message, Mapping)
        and message.get("role") == TextGenerationRole.ASSISTANT.value
        and isinstance(message.get("content"), str)
        and not message["content"].strip()
        and isinstance(message.get("thinking"), str)
        and bool(message["thinking"].strip())
    )


class _BoundedJSONRequestStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self.closed:
                raise httpx.StreamClosed()
            yield chunk

    async def aclose(self) -> None:
        self.closed = True
        self._chunks = ()


def _encode_bounded_json_request(
    payload: Mapping[str, Any],
    max_request_bytes: int,
) -> tuple[_BoundedJSONRequestStream, int]:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoded_chunks: list[bytes] = []
    encoded_length = 0
    for serialized_chunk in encoder.iterencode(payload):
        encoded_chunk = serialized_chunk.encode("utf-8")
        if len(encoded_chunk) > max_request_bytes - encoded_length:
            raise TextGenerationRequestTooLargeError(
                "local text generation is unavailable"
            )
        if encoded_chunk:
            encoded_chunks.append(encoded_chunk)
            encoded_length += len(encoded_chunk)
    return _BoundedJSONRequestStream(tuple(encoded_chunks)), encoded_length


def _measure_bounded_json_request(
    payload: Mapping[str, Any],
    max_request_bytes: int,
) -> int:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoded_length = 0
    for serialized_chunk in encoder.iterencode(payload):
        chunk_length = len(serialized_chunk.encode("utf-8"))
        if chunk_length > max_request_bytes - encoded_length:
            raise TextGenerationRequestTooLargeError(
                "local text generation is unavailable"
            )
        encoded_length += chunk_length
    return encoded_length


def _validated_local_model_allowlist(
    references: tuple[str, ...],
) -> frozenset[str]:
    if not isinstance(references, tuple):
        raise TypeError("local model allowlist must be a tuple")
    validated: set[str] = set()
    for reference in references:
        if not isinstance(reference, str):
            raise TypeError("local model allowlist entries must be strings")
        if not reference or reference != reference.strip():
            raise ValueError(
                "local model allowlist entries must be exact nonblank references"
            )
        if reference in validated:
            raise ValueError("local model allowlist entries must be unique")
        validated.add(reference)
    return frozenset(validated)


def _catalog_response_content_length(response: httpx.Response) -> int | None:
    values = [
        value
        for name, value in response.headers.raw
        if name.lower() == b"content-length"
    ]
    if not values:
        return None

    parsed: list[int] = []
    for value in values:
        if b"," in value:
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            )
        normalized = value.strip(b" \t")
        if not normalized or not normalized.isdigit():
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            )
        parsed.append(int(normalized))

    if len(set(parsed)) != 1:
        raise ModelRuntimeUnavailableError(
            "local model runtime inventory is unavailable"
        )
    return parsed[0]


def _validate_catalog_identity_content_encoding(response: httpx.Response) -> None:
    values = [
        value.strip(b" \t").lower()
        for name, value in response.headers.raw
        if name.lower() == b"content-encoding"
    ]
    if values and values != [b"identity"]:
        raise ModelRuntimeUnavailableError(
            "local model runtime inventory is unavailable"
        )


async def _request_catalog_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    max_response_bytes: int,
    request_json: Mapping[str, Any] | None = None,
) -> Any:
    request_kwargs: dict[str, Any] = {}
    if request_json is not None:
        request_kwargs["json"] = request_json

    response_body = bytearray()
    async with client.stream(
        method,
        path,
        headers={"Accept-Encoding": "identity"},
        **request_kwargs,
    ) as response:
        response.raise_for_status()
        _validate_catalog_identity_content_encoding(response)
        declared_length = _catalog_response_content_length(response)
        if (
            declared_length is not None
            and declared_length > max_response_bytes
        ):
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            )

        async for chunk in response.aiter_bytes():
            if len(chunk) > max_response_bytes - len(response_body):
                raise ModelRuntimeUnavailableError(
                    "local model runtime inventory is unavailable"
                )
            response_body.extend(chunk)

    try:
        try:
            return json.loads(response_body)
        except Exception as exc:
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            ) from exc
    finally:
        response_body.clear()


class OllamaModelDiscoveryRuntime:
    runtime_id = "ollama-local"
    supports_reference_selector = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        local_model_allowlist: tuple[str, ...] = (),
        *,
        max_response_bytes: int = 1_048_576,
        max_list_models: int = 256,
    ) -> None:
        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes,
            int,
        ):
            raise TypeError("catalog response cap must be an integer")
        if not 1 <= max_response_bytes <= MAX_OLLAMA_CATALOG_RESPONSE_BYTES:
            raise ValueError(
                "catalog response cap must be between 1 and "
                f"{MAX_OLLAMA_CATALOG_RESPONSE_BYTES}"
            )
        if isinstance(max_list_models, bool) or not isinstance(
            max_list_models,
            int,
        ):
            raise TypeError("catalog full-list model cap must be an integer")
        if not 1 <= max_list_models <= MAX_OLLAMA_CATALOG_LIST_MODELS:
            raise ValueError(
                "catalog full-list model cap must be between 1 and "
                f"{MAX_OLLAMA_CATALOG_LIST_MODELS}"
            )
        self.client = client
        self.max_response_bytes = max_response_bytes
        self.max_list_models = max_list_models
        self.local_model_allowlist = _validated_local_model_allowlist(
            local_model_allowlist
        )

    async def discover_models(
        self,
        *,
        reference_selector: Callable[[str], bool] | None = None,
    ) -> tuple[RuntimeModel, ...]:
        try:
            models = _parse_inventory(
                await _request_catalog_json(
                    self.client,
                    "GET",
                    "/api/tags",
                    max_response_bytes=self.max_response_bytes,
                ),
                self.local_model_allowlist,
                max_models=(
                    self.max_list_models
                    if reference_selector is None
                    else None
                ),
            )
            if reference_selector is not None:
                models = tuple(
                    model
                    for model in models
                    if reference_selector(model.reference)
                )
            discovered: list[RuntimeModel] = []
            for model in models:
                detail = _parse_model_details(
                    await _request_catalog_json(
                        self.client,
                        "POST",
                        "/api/show",
                        max_response_bytes=self.max_response_bytes,
                        request_json={"model": model.reference},
                    )
                )
                discovered.append(
                    replace(
                        model,
                        capabilities=detail.capabilities,
                        context_window=detail.context_window,
                        scale_class=detail.scale_class,
                    )
                )
            return tuple(discovered)
        except ModelRuntimeUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            ) from exc


class OllamaTextGenerationRuntime:
    runtime_id = "ollama-local"

    def __init__(
        self,
        client: httpx.AsyncClient,
        timeout_seconds: float,
        local_model_allowlist: tuple[str, ...] = (),
        *,
        max_request_bytes: int = 1_048_576,
        max_response_bytes: int = 262_144,
        keep_alive_seconds: int | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise TypeError("generation timeout must be numeric")
        try:
            is_finite = math.isfinite(timeout_seconds)
        except OverflowError:
            is_finite = False
        if not is_finite or timeout_seconds <= 0:
            raise ValueError("generation timeout must be positive and finite")
        if isinstance(max_request_bytes, bool) or not isinstance(
            max_request_bytes,
            int,
        ):
            raise TypeError("generation request cap must be an integer")
        if not 1 <= max_request_bytes <= MAX_OLLAMA_GENERATION_REQUEST_BYTES:
            raise ValueError(
                "generation request cap must be between 1 and "
                f"{MAX_OLLAMA_GENERATION_REQUEST_BYTES}"
            )
        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes,
            int,
        ):
            raise TypeError("generation response cap must be an integer")
        if not 1 <= max_response_bytes <= MAX_OLLAMA_GENERATION_RESPONSE_BYTES:
            raise ValueError(
                "generation response cap must be between 1 and "
                f"{MAX_OLLAMA_GENERATION_RESPONSE_BYTES}"
            )
        if keep_alive_seconds is not None and (
            isinstance(keep_alive_seconds, bool)
            or not isinstance(keep_alive_seconds, int)
            or not 0 <= keep_alive_seconds <= 3600
        ):
            raise ValueError("keep-alive seconds must be between 0 and 3600")
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.local_model_allowlist = _validated_local_model_allowlist(
            local_model_allowlist
        )
        self.keep_alive_seconds = keep_alive_seconds

    def _generation_payload(
        self,
        runtime_reference: str,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
        typical_p: float | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        stop_sequences: list[str] | None = None,
        thinking: bool | None = None,
    ) -> dict[str, Any]:
        if runtime_reference not in self.local_model_allowlist:
            raise TextGenerationRuntimeUnsupportedError(
                "model is not approved for local text generation"
            )
        use_thinking = (
            _supports_hidden_thinking(runtime_reference)
            if thinking is None
            else thinking
        )
        predict_tokens = max_output_tokens
        if use_thinking:
            predict_tokens = min(
                max_output_tokens + QWEN3_REASONING_HEADROOM_TOKENS,
                MAX_QWEN3_PREDICT_TOKENS,
            )
        options: dict[str, int | float | list[str]] = {
            "num_predict": predict_tokens,
        }
        if temperature is not None:
            options["temperature"] = temperature
        if seed is not None:
            options["seed"] = seed
        if top_p is not None:
            options["top_p"] = top_p
        if top_k is not None:
            options["top_k"] = top_k
        if min_p is not None:
            options["min_p"] = min_p
        if repeat_penalty is not None:
            options["repeat_penalty"] = repeat_penalty
        if repeat_last_n is not None:
            options["repeat_last_n"] = repeat_last_n
        if typical_p is not None:
            options["typical_p"] = typical_p
        if presence_penalty is not None:
            options["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            options["frequency_penalty"] = frequency_penalty
        if stop_sequences is not None:
            options["stop"] = stop_sequences
        payload: dict[str, Any] = {
            "model": runtime_reference,
            "messages": [
                {
                    "role": message.role.value,
                    "content": message.content,
                    **({"images": list(message.images)} if message.images else {}),
                }
                for message in messages
            ],
            "stream": False,
            # WORK STATION persists and exposes only the final answer. Qwen3
            # receives separate bounded headroom for its hidden reasoning;
            # other model families retain their ordinary bounded generation.
            "think": use_thinking,
            "options": options,
        }
        if self.keep_alive_seconds is not None:
            # Bound residency so sequential model choices swap instead of
            # accumulating several large models on the GPU.
            payload["keep_alive"] = self.keep_alive_seconds
        return payload

    async def _request_generation_payload(
        self,
        payload: Mapping[str, Any],
    ) -> Any:
        request_body: _BoundedJSONRequestStream | None = None
        response_body: bytearray | None = None
        try:
            request_body, request_body_length = _encode_bounded_json_request(
                payload,
                self.max_request_bytes,
            )
            async with self.client.stream(
                "POST",
                "/api/chat",
                headers={
                    "Accept-Encoding": "identity",
                    "Content-Length": str(request_body_length),
                    "Content-Type": "application/json",
                },
                content=request_body,
                timeout=self.timeout_seconds,
            ) as response:
                response.raise_for_status()
                _validate_identity_content_encoding(response)
                declared_length = _response_content_length(response)
                if (
                    declared_length is not None
                    and declared_length > self.max_response_bytes
                ):
                    raise TextGenerationRuntimeUnavailableError(
                        "local text runtime returned an invalid response"
                    )

                response_body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunk) > self.max_response_bytes - len(response_body):
                        raise TextGenerationRuntimeUnavailableError(
                            "local text runtime returned an invalid response"
                        )
                    response_body.extend(chunk)
                try:
                    return json.loads(response_body)
                except (ValueError, TypeError, RecursionError) as exc:
                    raise TextGenerationRuntimeUnavailableError(
                        "local text generation is unavailable"
                    ) from exc
        finally:
            if response_body is not None:
                response_body.clear()
            if request_body is not None:
                await request_body.aclose()

    def preflight_text(
        self,
        runtime_reference: str,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
        typical_p: float | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        stop_sequences: list[str] | None = None,
        thinking: bool | None = None,
    ) -> None:
        if not messages:
            raise ValueError("generation messages must not be empty")
        payload = self._generation_payload(
            runtime_reference,
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
            typical_p=typical_p,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            stop_sequences=stop_sequences,
            thinking=thinking,
        )
        _measure_bounded_json_request(payload, self.max_request_bytes)

    async def generate_text(
        self,
        runtime_reference: str,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
        typical_p: float | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        stop_sequences: list[str] | None = None,
        thinking: bool | None = None,
    ) -> TextGenerationResult:
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(
                temperature,
                (int, float),
            ):
                raise TypeError("temperature must be numeric")
            try:
                is_finite_temperature = math.isfinite(temperature)
            except OverflowError:
                is_finite_temperature = False
            if not is_finite_temperature or not 0.0 <= temperature <= 2.0:
                raise ValueError(
                    "temperature must be finite and between 0.0 and 2.0"
                )
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise TypeError("seed must be an integer")
            if not 0 <= seed <= 2_147_483_647:
                raise ValueError(
                    "seed must be between 0 and 2147483647"
                )
        if top_p is not None:
            if isinstance(top_p, bool) or not isinstance(
                top_p,
                (int, float),
            ):
                raise TypeError("top_p must be numeric")
            try:
                is_finite_top_p = math.isfinite(top_p)
            except OverflowError:
                is_finite_top_p = False
            if not is_finite_top_p or not 0.0 <= top_p <= 1.0:
                raise ValueError(
                    "top_p must be finite and between 0.0 and 1.0"
                )
        if top_k is not None:
            if isinstance(top_k, bool) or not isinstance(top_k, int):
                raise TypeError("top_k must be an integer")
            if not 1 <= top_k <= 100:
                raise ValueError("top_k must be between 1 and 100")
        if min_p is not None:
            if isinstance(min_p, bool) or not isinstance(
                min_p,
                (int, float),
            ):
                raise TypeError("min_p must be numeric")
            try:
                is_finite_min_p = math.isfinite(min_p)
            except OverflowError:
                is_finite_min_p = False
            if not is_finite_min_p or not 0.0 <= min_p <= 1.0:
                raise ValueError(
                    "min_p must be finite and between 0.0 and 1.0"
                )
        if repeat_penalty is not None:
            if isinstance(repeat_penalty, bool) or not isinstance(
                repeat_penalty,
                (int, float),
            ):
                raise TypeError("repeat_penalty must be numeric")
            try:
                is_finite_repeat_penalty = math.isfinite(repeat_penalty)
            except OverflowError:
                is_finite_repeat_penalty = False
            if (
                not is_finite_repeat_penalty
                or not 0.5 <= repeat_penalty <= 2.0
            ):
                raise ValueError(
                    "repeat_penalty must be finite and between 0.5 and 2.0"
                )
        if repeat_last_n is not None:
            if isinstance(repeat_last_n, bool) or not isinstance(
                repeat_last_n,
                int,
            ):
                raise TypeError("repeat_last_n must be an integer")
            if not 0 <= repeat_last_n <= 2_048:
                raise ValueError("repeat_last_n must be between 0 and 2048")
        if typical_p is not None:
            if isinstance(typical_p, bool) or not isinstance(
                typical_p,
                (int, float),
            ):
                raise TypeError("typical_p must be numeric")
            try:
                is_finite_typical_p = math.isfinite(typical_p)
            except OverflowError:
                is_finite_typical_p = False
            if not is_finite_typical_p or not 0.0 <= typical_p <= 1.0:
                raise ValueError(
                    "typical_p must be finite and between 0.0 and 1.0"
                )
        if presence_penalty is not None:
            if isinstance(presence_penalty, bool) or not isinstance(
                presence_penalty,
                (int, float),
            ):
                raise TypeError("presence_penalty must be numeric")
            try:
                is_finite_presence_penalty = math.isfinite(presence_penalty)
            except OverflowError:
                is_finite_presence_penalty = False
            if (
                not is_finite_presence_penalty
                or not -2.0 <= presence_penalty <= 2.0
            ):
                raise ValueError(
                    "presence_penalty must be finite and between -2.0 and 2.0"
                )
        if frequency_penalty is not None:
            if isinstance(frequency_penalty, bool) or not isinstance(
                frequency_penalty,
                (int, float),
            ):
                raise TypeError("frequency_penalty must be numeric")
            try:
                is_finite_frequency_penalty = math.isfinite(frequency_penalty)
            except OverflowError:
                is_finite_frequency_penalty = False
            if (
                not is_finite_frequency_penalty
                or not -2.0 <= frequency_penalty <= 2.0
            ):
                raise ValueError(
                    "frequency_penalty must be finite and between -2.0 and 2.0"
                )
        if stop_sequences is not None:
            if not isinstance(stop_sequences, list):
                raise TypeError("stop_sequences must be a list")
            if not 1 <= len(stop_sequences) <= 4:
                raise ValueError(
                    "stop_sequences must contain between 1 and 4 entries"
                )
            for sequence in stop_sequences:
                if not isinstance(sequence, str):
                    raise TypeError("stop_sequences entries must be strings")
                if not 1 <= len(sequence) <= 128:
                    raise ValueError(
                        "stop_sequences entries must contain between 1 and "
                        "128 characters"
                    )
        if thinking is not None and not isinstance(thinking, bool):
            raise TypeError("thinking must be a boolean or None")

        payload = self._generation_payload(
            runtime_reference,
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
            typical_p=typical_p,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            stop_sequences=stop_sequences,
            thinking=thinking,
        )
        try:
            response_payload = await self._request_generation_payload(payload)
            if payload["think"] is True and _reasoning_exhausted(response_payload):
                fallback_payload = self._generation_payload(
                    runtime_reference,
                    messages,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    seed=seed,
                    top_p=top_p,
                    top_k=top_k,
                    min_p=min_p,
                    repeat_penalty=repeat_penalty,
                    repeat_last_n=repeat_last_n,
                    typical_p=typical_p,
                    presence_penalty=presence_penalty,
                    frequency_penalty=frequency_penalty,
                    stop_sequences=stop_sequences,
                    thinking=False,
                )
                response_payload = await self._request_generation_payload(
                    fallback_payload
                )
            return _parse_generation(response_payload)
        except TextGenerationRuntimeUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise TextGenerationRuntimeUnavailableError(
                "local text generation is unavailable"
            ) from exc


def _response_content_length(response: httpx.Response) -> int | None:
    values = [
        value
        for name, value in response.headers.raw
        if name.lower() == b"content-length"
    ]
    if not values:
        return None

    parsed: list[int] = []
    for value in values:
        if b"," in value:
            raise TextGenerationRuntimeUnavailableError(
                "local text runtime returned an invalid response"
            )
        normalized = value.strip(b" \t")
        if not normalized or not normalized.isdigit():
            raise TextGenerationRuntimeUnavailableError(
                "local text runtime returned an invalid response"
            )
        parsed.append(int(normalized))

    if len(set(parsed)) != 1:
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    return parsed[0]


def _validate_identity_content_encoding(response: httpx.Response) -> None:
    values = [
        value.strip(b" \t").lower()
        for name, value in response.headers.raw
        if name.lower() == b"content-encoding"
    ]
    if values and values != [b"identity"]:
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )


def _parse_generation(payload: Any) -> TextGenerationResult:
    if not isinstance(payload, Mapping):
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    if payload.get("done") is not True:
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    if message.get("role") != TextGenerationRole.ASSISTANT.value:
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise TextGenerationRuntimeUnavailableError(
            "local text runtime returned an invalid response"
        )
    return TextGenerationResult(content=content)


def _parse_inventory(
    payload: Any,
    local_model_allowlist: frozenset[str],
    *,
    max_models: int | None = None,
) -> tuple[RuntimeModel, ...]:
    if not isinstance(payload, Mapping):
        raise ModelRuntimeUnavailableError(
            "local model runtime returned an invalid inventory"
        )
    models = payload.get("models")
    if not isinstance(models, list):
        raise ModelRuntimeUnavailableError(
            "local model runtime returned an invalid inventory"
        )

    parsed: list[RuntimeModel] = []
    seen_allowlisted_references: set[str] = set()
    list_limit_exceeded = False
    for item in models:
        if not isinstance(item, Mapping):
            raise ModelRuntimeUnavailableError(
                "local model runtime returned an invalid inventory"
            )
        reference = item.get("model", item.get("name"))
        if not isinstance(reference, str) or not reference.strip():
            raise ModelRuntimeUnavailableError(
                "local model runtime returned an invalid inventory"
            )
        if reference not in local_model_allowlist:
            continue
        if reference in seen_allowlisted_references:
            raise ModelRuntimeUnavailableError(
                "local model runtime returned an invalid inventory"
            )
        seen_allowlisted_references.add(reference)
        details = item.get("details", {})
        if not isinstance(details, Mapping):
            raise ModelRuntimeUnavailableError(
                "local model runtime returned an invalid inventory"
            )

        family = _safe_optional_text(details.get("family"))
        parameter_class = _safe_optional_text(details.get("parameter_size"))
        quantization = _safe_optional_text(details.get("quantization_level"))
        installed_size = _safe_positive_integer(item.get("size"))
        required_vram_bytes = (
            installed_size + max(1024**3, installed_size // 5)
            if installed_size is not None
            else None
        )
        # This is host/runtime RAM for a model whose weights fit in GPU VRAM,
        # not a CPU-offload budget. Capping the mmap/staging estimate keeps a
        # future full-GPU upgrade independent of the model's total weight size.
        required_ram_bytes = (
            min(
                installed_size + max(2 * 1024**3, installed_size // 4),
                64 * 1024**3,
            )
            if installed_size is not None
            else None
        )
        display_parts = [part for part in (family, parameter_class) if part]
        display_name = " ".join(display_parts) or "Local text model"
        if (
            max_models is not None
            and len(seen_allowlisted_references) > max_models
        ):
            list_limit_exceeded = True
            continue
        parsed.append(
            RuntimeModel(
                reference=reference,
                display_name=display_name,
                family=family,
                parameter_class=parameter_class,
                quantization=quantization,
                estimated_vram_bytes=required_vram_bytes,
                required_vram_bytes=required_vram_bytes,
                required_ram_bytes=required_ram_bytes,
                supports_multi_gpu=True,
            )
        )
    if list_limit_exceeded:
        raise ModelRuntimeUnavailableError(
            "local model runtime inventory is unavailable"
        )
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class _OllamaModelDetails:
    capabilities: tuple[ModelCapability, ...]
    context_window: int | None
    scale_class: ModelScaleClass | None


def _parse_model_details(payload: Any) -> _OllamaModelDetails:
    if not isinstance(payload, Mapping):
        raise ModelRuntimeUnavailableError(
            "local model runtime returned invalid model details"
        )
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(capability, str) for capability in capabilities
    ):
        raise ModelRuntimeUnavailableError(
            "local model runtime returned invalid model details"
        )

    mapping = {
        "completion": ModelCapability.TEXT_GENERATION,
        "vision": ModelCapability.VISION_INPUT,
        "embedding": ModelCapability.EMBEDDINGS,
        "tools": ModelCapability.TOOL_CALLING,
    }
    parsed_capabilities = tuple(
        sorted(
            {
                mapping[capability]
                for capability in capabilities
                if capability in mapping
            },
            key=lambda capability: capability.value,
        )
    )
    model_info = payload.get("model_info")
    context_window: int | None = None
    parameter_count: int | None = None
    if isinstance(model_info, Mapping):
        context_values = {
            value
            for key, value in model_info.items()
            if isinstance(key, str)
            and key.endswith(".context_length")
            and _safe_positive_integer(value) is not None
        }
        if len(context_values) == 1:
            context_window = next(iter(context_values))
        parameter_count = _safe_positive_integer(
            model_info.get("general.parameter_count")
        )
    return _OllamaModelDetails(
        capabilities=parsed_capabilities,
        context_window=context_window,
        scale_class=_scale_class(parameter_count),
    )


def _parse_capabilities(payload: Any) -> tuple[ModelCapability, ...]:
    return _parse_model_details(payload).capabilities


def _scale_class(parameter_count: int | None) -> ModelScaleClass | None:
    if parameter_count is None:
        return None
    billions = parameter_count / 1_000_000_000
    if 6 <= billions <= 10:
        return ModelScaleClass.SEVEN_TO_EIGHT_B
    if 10 < billions <= 20:
        return ModelScaleClass.FOURTEEN_B
    if 20 < billions <= 45:
        return ModelScaleClass.THIRTY_TO_THIRTY_FOUR_B
    if 45 < billions <= 90:
        return ModelScaleClass.SEVENTY_B
    if 90 < billions < 200:
        return ModelScaleClass.HUNDRED_B_PLUS
    if 200 <= billions < 500:
        return ModelScaleClass.TWO_HUNDRED_B_PLUS
    if 500 <= billions < 1000:
        return ModelScaleClass.FIVE_HUNDRED_B_PLUS
    if 1000 <= billions < 2000:
        return ModelScaleClass.ONE_THOUSAND_B_PLUS
    if billions >= 2000:
        return ModelScaleClass.TWO_THOUSAND_B
    return None


def _safe_positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _safe_optional_text(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_METADATA_PATTERN.fullmatch(value)
    ):
        return None
    return value[:255]
