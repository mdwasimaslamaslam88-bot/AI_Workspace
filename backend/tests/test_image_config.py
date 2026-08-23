from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _complete(root: Path) -> dict[str, object]:
    return {
        "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
        "COMFYUI_CHECKPOINT": root / "models/checkpoint.safetensors",
        "COMFYUI_INPUT_ROOT": root / "input",
        "COMFYUI_TEMP_ROOT": root / "temp",
        "COMFYUI_MODEL_REFERENCE": "stabilityai/sdxl@pinned",
    }


def test_comfyui_is_optional_bounded_and_loopback_only(tmp_path):
    defaults = Settings(_env_file=None)
    configured = Settings(_env_file=None, **_complete(tmp_path.resolve()))

    assert defaults.COMFYUI_BASE_URL is None
    assert configured.COMFYUI_BASE_URL.host == "127.0.0.1"
    assert configured.COMFYUI_TIMEOUT_SECONDS == 300.0
    assert configured.COMFYUI_MAX_ACTIVE_PER_PROCESS == 1


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com:8188",
        "http://user:secret@127.0.0.1:8188",
        "http://127.0.0.1:8188/private",
        "http://127.0.0.1:8188?token=secret",
    ],
)
def test_comfyui_url_rejects_non_loopback_credentials_and_paths(tmp_path, value):
    values = _complete(tmp_path.resolve())
    values["COMFYUI_BASE_URL"] = value

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_comfyui_configuration_fails_closed_when_partial():
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            COMFYUI_BASE_URL="http://127.0.0.1:8188",
        )


@pytest.mark.parametrize(
    "field",
    ["COMFYUI_CHECKPOINT", "COMFYUI_INPUT_ROOT", "COMFYUI_TEMP_ROOT"],
)
def test_comfyui_paths_reject_source_tree(tmp_path, field):
    values = _complete(tmp_path.resolve())
    project_root = Path(__file__).resolve().parents[2]
    values[field] = project_root / "unsafe"

    with pytest.raises(ValidationError, match="outside the source tree"):
        Settings(_env_file=None, **values)
