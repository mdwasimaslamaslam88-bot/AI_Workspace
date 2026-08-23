from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.audio import SpeechRuntimeInputError, SpeechRuntimeUnavailableError
from app.runtimes.faster_whisper import (
    MAX_STT_INPUT_BYTES,
    MAX_STT_RESPONSE_BYTES,
    FasterWhisperSpeechRecognitionRuntime,
)


def _runtime_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    model = tmp_path / "model"
    libraries = tmp_path / "libraries"
    python.write_bytes(b"fixture")
    worker.write_bytes(b"fixture")
    model.mkdir()
    libraries.mkdir()
    return python, worker, model, libraries


def test_faster_whisper_preserves_virtual_environment_python_symlink(tmp_path):
    system_python, worker, model, _libraries = _runtime_paths(tmp_path)
    environment = tmp_path / "environment"
    environment.mkdir()
    virtual_python = environment / "python"
    virtual_python.symlink_to(system_python)

    runtime = FasterWhisperSpeechRecognitionRuntime(
        virtual_python,
        worker,
        model,
        model_reference="small.en@pinned",
    )

    assert runtime.python == virtual_python.absolute()
    assert runtime.python.resolve() == system_python.resolve()


@pytest.mark.asyncio
async def test_faster_whisper_is_offline_bounded_and_cleans_audio(
    tmp_path, monkeypatch
):
    python, worker, model, libraries = _runtime_paths(tmp_path)
    observed: dict[str, object] = {}

    class Process:
        returncode = 0
        stdout = asyncio.StreamReader()

        def __init__(self):
            self.stdout.feed_data(
                b'{"text":"local transcript","language":"en",'
                b'"duration_seconds":1.25}'
            )
            self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def create_subprocess(*arguments, **options):
        observed["arguments"] = arguments
        observed["options"] = options
        audio = Path(arguments[arguments.index("--audio") + 1])
        observed["audio"] = audio
        assert audio.read_bytes() == b"RIFF-private-audio"
        assert audio.stat().st_mode & 0o777 == 0o600
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    runtime = FasterWhisperSpeechRecognitionRuntime(
        python,
        worker,
        model,
        model_reference="small.en@pinned",
        library_directories=(libraries,),
    )

    result = await runtime.transcribe(b"RIFF-private-audio")

    assert result.text == "local transcript"
    assert result.language == "en"
    assert result.duration_seconds == 1.25
    options = observed["options"]
    assert options["stderr"] is asyncio.subprocess.DEVNULL
    assert options["env"]["HF_HUB_OFFLINE"] == "1"
    assert options["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert options["env"]["LD_LIBRARY_PATH"] == str(libraries)
    assert "DATABASE_URL" not in options["env"]
    assert not observed["audio"].exists()


@pytest.mark.parametrize("value", [b"", b"x" * (MAX_STT_INPUT_BYTES + 1)])
@pytest.mark.asyncio
async def test_faster_whisper_rejects_audio_outside_bound(
    tmp_path, monkeypatch, value
):
    python, worker, model, _libraries = _runtime_paths(tmp_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", pytest.fail)
    runtime = FasterWhisperSpeechRecognitionRuntime(
        python,
        worker,
        model,
        model_reference="small.en@pinned",
    )

    with pytest.raises(SpeechRuntimeInputError):
        await runtime.transcribe(value)


@pytest.mark.asyncio
async def test_faster_whisper_stops_reading_stdout_at_fixed_bound():
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * (MAX_STT_RESPONSE_BYTES + 1))
    reader.feed_eof()

    with pytest.raises(SpeechRuntimeUnavailableError):
        await FasterWhisperSpeechRecognitionRuntime._read_bounded_stdout(reader)


@pytest.mark.asyncio
async def test_faster_whisper_cancellation_terminates_child_and_cleans_audio(
    tmp_path, monkeypatch
):
    python, worker, model, _libraries = _runtime_paths(tmp_path)
    started = asyncio.Event()
    observed: dict[str, object] = {}

    class BlockingReader:
        async def read(self, _size):
            started.set()
            await asyncio.Future()

    class Process:
        returncode = None
        stdout = BlockingReader()

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            return self.returncode

    async def create_subprocess(*arguments, **_options):
        observed["audio"] = Path(arguments[arguments.index("--audio") + 1])
        observed["process"] = Process()
        return observed["process"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    runtime = FasterWhisperSpeechRecognitionRuntime(
        python,
        worker,
        model,
        model_reference="small.en@pinned",
    )
    operation = asyncio.create_task(runtime.transcribe(b"RIFF-private-audio"))
    await started.wait()

    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation

    assert observed["process"].returncode == -15
    assert not observed["audio"].exists()
