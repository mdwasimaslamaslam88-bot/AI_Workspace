"""Adapters for fully local AI runtimes."""

from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)

__all__ = ["OllamaModelDiscoveryRuntime", "OllamaTextGenerationRuntime"]
