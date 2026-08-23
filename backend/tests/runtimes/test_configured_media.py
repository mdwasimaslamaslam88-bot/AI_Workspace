from pathlib import Path

import pytest

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelModality,
)
from app.runtimes.configured_media import (
    ConfiguredMediaModel,
    ConfiguredMediaModelDiscoveryRuntime,
)


def _model(tmp_path: Path) -> ConfiguredMediaModel:
    return ConfiguredMediaModel(
        reference="en_US-lessac-medium@pinned",
        display_name="Piper Lessac Medium",
        modality=ModelModality.AUDIO,
        family="Piper",
        parameter_class="medium",
        capabilities=(ModelCapability.SPEECH_SYNTHESIS,),
        required_vram_bytes=0,
        required_ram_bytes=512 * 1024**2,
        required_files=(tmp_path / "voice.onnx",),
    )


@pytest.mark.asyncio
async def test_configured_media_inventory_reports_exact_installed_files(tmp_path):
    model = _model(tmp_path)
    runtime = ConfiguredMediaModelDiscoveryRuntime("piper", (model,))

    missing = (await runtime.discover_models())[0]
    model.required_files[0].write_bytes(b"model")
    installed = (await runtime.discover_models())[0]

    assert missing.installed is False
    assert missing.availability is ModelAvailability.UNAVAILABLE
    assert installed.installed is True
    assert installed.availability is ModelAvailability.AVAILABLE
    assert installed.capabilities == (ModelCapability.SPEECH_SYNTHESIS,)
    assert installed.required_vram_bytes == 0


@pytest.mark.asyncio
async def test_configured_media_inventory_applies_reference_selector(tmp_path):
    model = _model(tmp_path)
    runtime = ConfiguredMediaModelDiscoveryRuntime("piper", (model,))

    assert await runtime.discover_models(
        reference_selector=lambda reference: reference == "different"
    ) == ()
