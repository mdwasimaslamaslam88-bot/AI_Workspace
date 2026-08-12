from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Protocol, runtime_checkable


_RUNTIME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PUBLIC_MODEL_ID_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"
)
_PUBLIC_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,254}$")


class ModelModality(StrEnum):
    TEXT = "text"


class ModelCapability(StrEnum):
    TEXT_GENERATION = "text_generation"
    CHAT = "chat"
    CODE = "code"
    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    VISION_INPUT = "vision_input"
    EMBEDDINGS = "embeddings"


class ModelAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ModelRuntimeUnavailableError(RuntimeError):
    """A configured local runtime could not provide a safe model inventory."""


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    if not _PUBLIC_METADATA_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must contain only safe public metadata")
    return value


def normalize_capabilities(
    values: tuple[ModelCapability | str, ...],
) -> tuple[ModelCapability, ...]:
    normalized: set[ModelCapability] = set()
    for value in values:
        if isinstance(value, ModelCapability):
            normalized.add(value)
            continue
        if not isinstance(value, str):
            raise TypeError("model capabilities must be strings")
        candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
        try:
            normalized.add(ModelCapability(candidate))
        except ValueError:
            continue
    return tuple(sorted(normalized, key=lambda capability: capability.value))


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    """Provider result retaining its opaque reference below the API boundary."""

    reference: str
    display_name: str
    modality: ModelModality = ModelModality.TEXT
    family: str | None = None
    parameter_class: str | None = None
    capabilities: tuple[ModelCapability | str, ...] = ()
    context_window: int | None = None
    quantization: str | None = None
    estimated_vram_bytes: int | None = None
    availability: ModelAvailability = ModelAvailability.AVAILABLE

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str):
            raise TypeError("runtime model reference must be a string")
        if not self.reference.strip():
            raise ValueError("runtime model reference must not be blank")
        if not isinstance(self.display_name, str):
            raise TypeError("model display_name must be a string")
        if not _PUBLIC_METADATA_PATTERN.fullmatch(self.display_name):
            raise ValueError(
                "model display_name must contain only safe public metadata"
            )
        if not isinstance(self.modality, ModelModality):
            raise TypeError("model modality must be a ModelModality")
        if not isinstance(self.availability, ModelAvailability):
            raise TypeError("model availability must be a ModelAvailability")
        _optional_text(self.family, "model family")
        _optional_text(self.parameter_class, "model parameter_class")
        _optional_text(self.quantization, "model quantization")
        if self.context_window is not None and (
            isinstance(self.context_window, bool)
            or not isinstance(self.context_window, int)
            or self.context_window < 1
        ):
            raise ValueError("model context_window must be a positive integer")
        if self.estimated_vram_bytes is not None and (
            isinstance(self.estimated_vram_bytes, bool)
            or not isinstance(self.estimated_vram_bytes, int)
            or self.estimated_vram_bytes < 1
        ):
            raise ValueError(
                "model estimated_vram_bytes must be a positive integer"
            )
        object.__setattr__(
            self,
            "capabilities",
            normalize_capabilities(self.capabilities),
        )


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    model_id: str
    display_name: str
    runtime_id: str
    modality: ModelModality
    family: str | None
    parameter_class: str | None
    capabilities: tuple[ModelCapability, ...]
    context_window: int | None
    quantization: str | None
    estimated_vram_bytes: int | None
    availability: ModelAvailability

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            self.model_id
        ):
            raise ValueError("model_id must be a runtime-namespaced public ID")
        if not isinstance(self.runtime_id, str) or not _RUNTIME_ID_PATTERN.fullmatch(
            self.runtime_id
        ):
            raise ValueError("runtime_id must be a normalized runtime identifier")
        if not self.model_id.startswith(f"{self.runtime_id}:"):
            raise ValueError("model_id namespace must match runtime_id")
        validated = RuntimeModel(
            reference="internal-validation-only",
            display_name=self.display_name,
            modality=self.modality,
            family=self.family,
            parameter_class=self.parameter_class,
            capabilities=self.capabilities,
            context_window=self.context_window,
            quantization=self.quantization,
            estimated_vram_bytes=self.estimated_vram_bytes,
            availability=self.availability,
        )


        object.__setattr__(self, "capabilities", validated.capabilities)
@runtime_checkable
class ModelDiscoveryRuntime(Protocol):
    runtime_id: str

    async def discover_models(self) -> tuple[RuntimeModel, ...]: ...


class ModelCatalog:
    def __init__(
        self,
        runtimes: tuple[ModelDiscoveryRuntime, ...] = (),
    ) -> None:
        runtime_ids: set[str] = set()
        for runtime in runtimes:
            runtime_id = runtime.runtime_id
            if not isinstance(runtime_id, str) or not _RUNTIME_ID_PATTERN.fullmatch(
                runtime_id
            ):
                raise ValueError("runtime_id must be a normalized runtime identifier")
            if runtime_id in runtime_ids:
                raise ValueError(f"duplicate runtime_id: {runtime_id}")
            runtime_ids.add(runtime_id)
        self.runtimes = runtimes

    async def list_models(self) -> tuple[ModelDescriptor, ...]:
        descriptors: dict[str, ModelDescriptor] = {}
        for runtime in self.runtimes:
            for model in await runtime.discover_models():
                if not isinstance(model, RuntimeModel):
                    raise TypeError("runtime discovery must return RuntimeModel values")
                model_id = _public_model_id(runtime.runtime_id, model.reference)
                if model_id in descriptors:
                    raise ValueError(f"duplicate public model_id: {model_id}")
                descriptors[model_id] = ModelDescriptor(
                    model_id=model_id,
                    display_name=model.display_name,
                    runtime_id=runtime.runtime_id,
                    modality=model.modality,
                    family=model.family,
                    parameter_class=model.parameter_class,
                    capabilities=model.capabilities,
                    context_window=model.context_window,
                    quantization=model.quantization,
                    estimated_vram_bytes=model.estimated_vram_bytes,
                    availability=model.availability,
                )
        return tuple(
            sorted(
                descriptors.values(),
                key=lambda item: (item.display_name.casefold(), item.model_id),
            )
        )


def _public_model_id(runtime_id: str, runtime_reference: str) -> str:
    digest = hashlib.sha256(
        f"{runtime_id}\0{runtime_reference}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{runtime_id}:{digest}"
