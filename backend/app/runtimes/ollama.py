from collections.abc import Mapping
import re
from typing import Any

import httpx

from app.ai.catalog import ModelRuntimeUnavailableError, RuntimeModel


_SAFE_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,254}$")


class OllamaModelDiscoveryRuntime:
    runtime_id = "ollama-local"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def discover_models(self) -> tuple[RuntimeModel, ...]:
        try:
            response = await self.client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
            return _parse_inventory(payload)
        except ModelRuntimeUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelRuntimeUnavailableError(
                "local model runtime inventory is unavailable"
            ) from exc


def _parse_inventory(payload: Any) -> tuple[RuntimeModel, ...]:
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
