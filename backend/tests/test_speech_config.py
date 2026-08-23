from pathlib import Path

import pytest
from pydantic import ValidationError

import app.core.lifespan as lifespan_module
from app.core.config import Settings


def test_speech_runtimes_are_optional_and_bounded_by_default():
    configured = Settings(_env_file=None)

    assert configured.STT_PYTHON is None
    assert configured.STT_MODEL_ROOT is None
    assert configured.TTS_PIPER_BINARY is None
    assert configured.STT_MAX_ACTIVE_PER_PROCESS == 1
    assert configured.TTS_MAX_ACTIVE_PER_PROCESS == 1


def test_speech_runtime_groups_accept_private_absolute_paths(tmp_path):
    root = tmp_path.resolve()
    configured = Settings(
        _env_file=None,
        STT_PYTHON=root / "stt/python",
        STT_MODEL_ROOT=root / "stt/model",
        STT_MODEL_REFERENCE="Systran/small.en@pinned",
        STT_LIBRARY_DIRECTORIES=(root / "cuda/cublas", root / "cuda/cudnn"),
        TTS_PIPER_BINARY=root / "tts/piper",
        TTS_VOICE_MODEL=root / "tts/voice.onnx",
        TTS_VOICE_CONFIG=root / "tts/voice.onnx.json",
        TTS_VOICE_REFERENCE="en_US-lessac-medium@pinned",
    )

    assert configured.STT_DEVICE == "cuda"
    assert configured.STT_COMPUTE_TYPE == "float16"
    assert configured.STT_LIBRARY_DIRECTORIES == (
        root / "cuda/cublas",
        root / "cuda/cudnn",
    )


@pytest.mark.parametrize(
    "values",
    [
        {"STT_PYTHON": "/runtime/python"},
        {"STT_MODEL_ROOT": "/runtime/model"},
        {"STT_MODEL_REFERENCE": "small.en"},
        {"TTS_PIPER_BINARY": "/runtime/piper"},
        {"TTS_VOICE_MODEL": "/runtime/voice.onnx"},
        {"TTS_VOICE_CONFIG": "/runtime/voice.json"},
        {"TTS_VOICE_REFERENCE": "voice"},
    ],
)
def test_speech_runtime_configuration_fails_closed_when_partial(values):
    with pytest.raises(ValidationError, match="configured together"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "field",
    [
        "STT_PYTHON",
        "STT_MODEL_ROOT",
        "TTS_PIPER_BINARY",
        "TTS_VOICE_MODEL",
        "TTS_VOICE_CONFIG",
    ],
)
def test_speech_runtime_paths_reject_source_tree(field):
    project_root = Path(__file__).resolve().parents[2]
    values = {
        "STT_PYTHON": "/runtime/python",
        "STT_MODEL_ROOT": "/runtime/model",
        "STT_MODEL_REFERENCE": "small.en",
        "TTS_PIPER_BINARY": "/runtime/piper",
        "TTS_VOICE_MODEL": "/runtime/voice.onnx",
        "TTS_VOICE_CONFIG": "/runtime/voice.json",
        "TTS_VOICE_REFERENCE": "voice",
    }
    values[field] = project_root / "unsafe"

    with pytest.raises(ValidationError, match="outside the source tree"):
        Settings(_env_file=None, **values)


def test_stt_library_directories_reject_duplicates(tmp_path):
    path = tmp_path.resolve()
    with pytest.raises(ValidationError, match="unique"):
        Settings(
            _env_file=None,
            STT_LIBRARY_DIRECTORIES=(path, path),
        )


def test_stt_python_preserves_virtual_environment_symlink(tmp_path):
    target = tmp_path / "python3"
    target.write_bytes(b"interpreter")
    virtual_python = tmp_path / "venv-python"
    virtual_python.symlink_to(target)

    configured = Settings(
        _env_file=None,
        STT_PYTHON=virtual_python,
        STT_MODEL_ROOT=tmp_path / "model",
        STT_MODEL_REFERENCE="small.en@pinned",
    )

    assert configured.STT_PYTHON == virtual_python.absolute()
    assert configured.STT_PYTHON.resolve() == target.resolve()


@pytest.mark.parametrize(
    ("device", "required_vram_bytes"),
    [("cuda", 2 * 1024**3), ("cpu", 0)],
)
def test_stt_discovery_admission_tracks_configured_device(
    tmp_path, monkeypatch, device, required_vram_bytes
):
    python = tmp_path / "python"
    python.write_bytes(b"isolated runtime")
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(lifespan_module.settings, "STT_PYTHON", python)
    monkeypatch.setattr(lifespan_module.settings, "STT_MODEL_ROOT", model)
    monkeypatch.setattr(
        lifespan_module.settings, "STT_MODEL_REFERENCE", "small.en@pinned"
    )
    monkeypatch.setattr(lifespan_module.settings, "STT_LIBRARY_DIRECTORIES", ())
    monkeypatch.setattr(lifespan_module.settings, "STT_DEVICE", device)
    monkeypatch.setattr(lifespan_module.settings, "TTS_PIPER_BINARY", None)
    monkeypatch.setattr(lifespan_module.settings, "TTS_VOICE_MODEL", None)
    monkeypatch.setattr(lifespan_module.settings, "TTS_VOICE_CONFIG", None)
    monkeypatch.setattr(lifespan_module.settings, "TTS_VOICE_REFERENCE", None)

    discovery, recognition, synthesis = lifespan_module._speech_runtimes()

    assert len(discovery) == 1
    assert discovery[0].models[0].required_vram_bytes == required_vram_bytes
    assert recognition is not None
    assert synthesis is None
