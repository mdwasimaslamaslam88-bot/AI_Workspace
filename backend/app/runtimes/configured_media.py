from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelModality,
    RuntimeModel,
)


@dataclass(frozen=True, slots=True)
class ConfiguredMediaModel:
    reference: str
    display_name: str
    modality: ModelModality
    family: str
    parameter_class: str
    capabilities: tuple[ModelCapability, ...]
    required_vram_bytes: int
    required_ram_bytes: int
    required_files: tuple[Path, ...] = ()
    required_directories: tuple[Path, ...] = ()


class ConfiguredMediaModelDiscoveryRuntime:
    """Inventory explicitly configured local media files without loading them."""

    supports_reference_selector = True

    def __init__(
        self,
        runtime_id: str,
        models: tuple[ConfiguredMediaModel, ...],
    ) -> None:
        self.runtime_id = runtime_id
        self.models = models

    async def discover_models(
        self,
        *,
        reference_selector: Callable[[str], bool] | None = None,
    ) -> tuple[RuntimeModel, ...]:
        selected = tuple(
            model
            for model in self.models
            if reference_selector is None or reference_selector(model.reference)
        )
        installed = await asyncio.gather(
            *(asyncio.to_thread(self._is_installed, model) for model in selected)
        )
        return tuple(
            RuntimeModel(
                reference=model.reference,
                display_name=model.display_name,
                modality=model.modality,
                family=model.family,
                parameter_class=model.parameter_class,
                capabilities=model.capabilities,
                required_vram_bytes=model.required_vram_bytes,
                required_ram_bytes=model.required_ram_bytes,
                installed=is_installed,
                availability=(
                    ModelAvailability.AVAILABLE
                    if is_installed
                    else ModelAvailability.UNAVAILABLE
                ),
            )
            for model, is_installed in zip(selected, installed, strict=True)
        )

    @staticmethod
    def _is_installed(model: ConfiguredMediaModel) -> bool:
        try:
            return all(
                path.is_absolute() and path.resolve(strict=True).is_file()
                for path in model.required_files
            ) and all(
                path.is_absolute() and path.resolve(strict=True).is_dir()
                for path in model.required_directories
            )
        except OSError:
            return False
