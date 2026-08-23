from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import wave

import pytest

from app.audio import SpeechRuntimeInputError
from app.runtimes.piper import (
    MAX_TTS_INPUT_CHARACTERS,
    PiperSpeechSynthesisRuntime,
)


def _wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22_050)
        audio.writeframes(b"\0\0" * 2_205)
    return output.getvalue()


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    binary = tmp_path / "piper"
    model = tmp_path / "voice.onnx"
    config = tmp_path / "voice.onnx.json"
    for path in (binary, model, config):
        path.write_bytes(b"fixture")
    return binary, model, config


@pytest.mark.asyncio
async def test_piper_uses_stdin_bounded_output_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    binary, model, config = _files(tmp_path)
    observed: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self, value):
            observed["stdin"] = value
            return b"", b""

    async def create_subprocess(*arguments, **options):
        observed["arguments"] = arguments
        observed["options"] = options
        output = Path(arguments[arguments.index("--output_file") + 1])
        observed["output"] = output
        output.write_bytes(_wav_bytes())
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    runtime = PiperSpeechSynthesisRuntime(
        binary,
        model,
        config,
        model_reference="en_US-lessac-medium",
    )

    result = await runtime.synthesize("Private synthesis input")

    assert result == _wav_bytes()
    assert observed["stdin"] == b"Private synthesis input\n"
    assert "Private synthesis input" not in observed["arguments"]
    assert observed["options"]["stderr"] is asyncio.subprocess.DEVNULL
    assert set(observed["options"]["env"]) == {
        "PATH",
        "PYTHONNOUSERSITE",
        "LC_ALL",
    }
    assert not observed["output"].exists()


@pytest.mark.parametrize(
    "value",
    ["", " ", "unsafe\x00text", "x" * (MAX_TTS_INPUT_CHARACTERS + 1)],
)
@pytest.mark.asyncio
async def test_piper_rejects_invalid_text_before_starting_process(
    tmp_path, monkeypatch, value
):
    binary, model, config = _files(tmp_path)
    create_subprocess = monkeypatch.setattr(
        asyncio, "create_subprocess_exec", pytest.fail
    )
    del create_subprocess
    runtime = PiperSpeechSynthesisRuntime(
        binary,
        model,
        config,
        model_reference="en_US-lessac-medium",
    )

    with pytest.raises(SpeechRuntimeInputError):
        await runtime.synthesize(value)


@pytest.mark.asyncio
async def test_piper_cancellation_terminates_child_and_cleans_output(
    tmp_path, monkeypatch
):
    binary, model, config = _files(tmp_path)
    started = asyncio.Event()
    observed: dict[str, object] = {}

    class Process:
        returncode = None

        async def communicate(self, _value):
            started.set()
            await asyncio.Future()

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            return self.returncode

    async def create_subprocess(*arguments, **_options):
        observed["output"] = Path(
            arguments[arguments.index("--output_file") + 1]
        )
        observed["process"] = Process()
        return observed["process"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    runtime = PiperSpeechSynthesisRuntime(
        binary,
        model,
        config,
        model_reference="en_US-lessac-medium",
    )
    operation = asyncio.create_task(runtime.synthesize("Cancel private speech"))
    await started.wait()

    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation

    assert observed["process"].returncode == -15
    assert not observed["output"].exists()
