from collections.abc import Mapping
import math
import re
from typing import Any

import httpx

from app.ai.catalog import ModelRuntimeUnavailableError, RuntimeModel
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationResult,
    TextGenerationRole,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)


_SAFE_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,254}$")


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


class OllamaModelDiscoveryRuntime:
    runtime_id = "ollama-local"

    def __init__(
        self,
        client: httpx.AsyncClient,
        local_model_allowlist: tuple[str, ...] = (),
    ) -> None:
        self.client = client
        self.local_model_allowlist = _validated_local_model_allowlist(
            local_model_allowlist
        )

    async def discover_models(self) -> tuple[RuntimeModel, ...]:
        try:
            response = await self.client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
            return _parse_inventory(payload, self.local_model_allowlist)
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
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.local_model_allowlist = _validated_local_model_allowlist(
            local_model_allowlist
        )

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

        if runtime_reference not in self.local_model_allowlist:
            raise TextGenerationRuntimeUnsupportedError(
                "model is not approved for local text generation"
            )
        options: dict[str, int | float] = {
            "num_predict": max_output_tokens,
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
        try:
            response = await self.client.post(
                "/api/chat",
                json={
                    "model": runtime_reference,
                    "messages": [
                        {
                            "role": message.role.value,
                            "content": message.content,
                        }
                        for message in messages
                    ],
                    "stream": False,
                    "options": options,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return _parse_generation(response.json())
        except TextGenerationRuntimeUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise TextGenerationRuntimeUnavailableError(
                "local text generation is unavailable"
            ) from exc


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
        details = item.get("details", {})
        if not isinstance(details, Mapping):
            raise ModelRuntimeUnavailableError(
                "local model runtime returned an invalid inventory"
            )

        family = _safe_optional_text(details.get("family"))
        parameter_class = _safe_optional_text(details.get("parameter_size"))
        quantization = _safe_optional_text(details.get("quantization_level"))
        capabilities_value = item.get("capabilities", ())
        if not isinstance(capabilities_value, list):
            capabilities_value = ()
        capabilities = tuple(
            value for value in capabilities_value if isinstance(value, str)
        )
        display_parts = [part for part in (family, parameter_class) if part]
        display_name = " ".join(display_parts) or "Local text model"
        parsed.append(
            RuntimeModel(
                reference=reference,
                display_name=display_name,
                family=family,
                parameter_class=parameter_class,
                capabilities=capabilities,
                quantization=quantization,
            )
        )
    return tuple(parsed)


def _safe_optional_text(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_METADATA_PATTERN.fullmatch(value)
    ):
        return None
    return value[:255]
