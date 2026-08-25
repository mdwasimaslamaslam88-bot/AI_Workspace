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
from app.ai.routing import (
    InferenceMode,
    ModelRoutingDecision,
    ModelRoutingUnavailableError,
    ModelTask,
    TaskAwareModelRouter,
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
    "InferenceMode",
    "ModelRoutingDecision",
    "ModelRoutingUnavailableError",
    "ModelTask",
    "TaskAwareModelRouter",
    "TextGenerationMessage",
    "TextGenerationResult",
    "TextGenerationRole",
    "TextGenerationRouter",
    "TextGenerationRuntime",
    "TextGenerationRuntimeUnavailableError",
    "TextGenerationRuntimeUnsupportedError",
]
