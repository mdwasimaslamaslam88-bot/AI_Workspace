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
    ) -> TextGenerationResult:
        if runtime_reference not in self.local_model_allowlist:
            raise TextGenerationRuntimeUnsupportedError(
                "model is not approved for local text generation"
            )
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
                    "options": {
                        "num_predict": max_output_tokens,
                    },
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
