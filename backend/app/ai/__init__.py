"""Runtime-neutral local AI model catalog contracts."""

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    ModelDescriptor,
    ModelDiscoveryRuntime,
    ModelModality,
    ModelRuntimeUnavailableError,
    ResolvedModel,
    RuntimeModel,
)
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationResult,
    TextGenerationRole,
    TextGenerationRouter,
    TextGenerationRuntime,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)

__all__ = [
    "ModelAvailability",
    "ModelCapability",
    "ModelCatalog",
    "ModelDescriptor",
    "ModelDiscoveryRuntime",
    "ModelModality",
    "ModelRuntimeUnavailableError",
    "ResolvedModel",
    "RuntimeModel",
    "TextGenerationMessage",
    "TextGenerationResult",
    "TextGenerationRole",
    "TextGenerationRouter",
    "TextGenerationRuntime",
    "TextGenerationRuntimeUnavailableError",
    "TextGenerationRuntimeUnsupportedError",
]
