"""Runtime-neutral local AI model catalog contracts."""

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    ModelDescriptor,
    ModelDiscoveryRuntime,
    ModelModality,
    ModelRuntimeUnavailableError,
    RuntimeModel,
)

__all__ = [
    "ModelAvailability",
    "ModelCapability",
    "ModelCatalog",
    "ModelDescriptor",
    "ModelDiscoveryRuntime",
    "ModelModality",
    "ModelRuntimeUnavailableError",
    "RuntimeModel",
]
