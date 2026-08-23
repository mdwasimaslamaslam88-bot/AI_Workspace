from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from app.audio import (
    SpeechRuntimeInputError,
    SpeechRuntimeUnavailableError,
    TranscriptionResult,
)


MAX_STT_INPUT_BYTES = 10 * 1024 * 1024
MAX_STT_RESPONSE_BYTES = 32 * 1024
MAX_STT_TIMEOUT_SECONDS = 300.0
_ALLOWED_COMPUTE_TYPES = frozenset(
    {"float16", "int8_float16", "int8", "float32"}
)


def _require_local_path(
    path: Path,
    field_name: str,
    *,
    directory: bool,
    preserve_symlink: bool = False,
) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")
    resolved = path.resolve(strict=True)
    if directory and not resolved.is_dir():
        raise ValueError(f"{field_name} must be a directory")
    if not directory and not resolved.is_file():
        raise ValueError(f"{field_name} must be a regular file")
    return Path(os.path.abspath(path)) if preserve_symlink else resolved


class FasterWhisperSpeechRecognitionRuntime:
    runtime_id = "faster_whisper"

    def __init__(
        self,
        python: Path,
        worker: Path,
        model_directory: Path,
        *,
        model_reference: str,
        device: str = "cuda",
        compute_type: str = "float16",
        library_directories: tuple[Path, ...] = (),
        timeout_seconds: float = 120.0,
        max_active: int = 1,
    ) -> None:
        if not isinstance(model_reference, str) or not model_reference.strip():
            raise ValueError("faster-whisper model reference must be nonblank")
        if device not in {"cuda", "cpu"}:
            raise ValueError("faster-whisper device is invalid")
        if compute_type not in _ALLOWED_COMPUTE_TYPES:
            raise ValueError("faster-whisper compute type is invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_STT_TIMEOUT_SECONDS
        ):
            raise ValueError("faster-whisper timeout is outside its bound")
        if isinstance(max_active, bool) or not isinstance(max_active, int):
            raise TypeError("faster-whisper concurrency must be an integer")
        if not 1 <= max_active <= 2:
            raise ValueError("faster-whisper concurrency is outside its bound")
        # A virtual environment's Python is normally a symlink. Launching its
        # resolved system target would silently discard that environment and
        # its isolated faster-whisper dependencies.
        self.python = _require_local_path(
            python,
            "STT Python",
            directory=False,
            preserve_symlink=True,
        )
        self.worker = _require_local_path(worker, "STT worker", directory=False)
        self.model_directory = _require_local_path(
            model_directory, "STT model", directory=True
        )
        self.library_directories = tuple(
            _require_local_path(path, "STT library directory", directory=True)
            for path in library_directories
        )
        self.model_reference = model_reference
        self.device = device
        self.compute_type = compute_type
        self.timeout_seconds = float(timeout_seconds)
        self._admission = asyncio.Semaphore(max_active)

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        if not isinstance(audio, bytes):
            raise TypeError("speech recognition input must be bytes")
        if not 0 < len(audio) <= MAX_STT_INPUT_BYTES:
            raise SpeechRuntimeInputError("speech recognition input is outside its bound")
        async with self._admission:
            return await self._transcribe_admitted(audio)

    async def _transcribe_admitted(self, audio: bytes) -> TranscriptionResult:
        with tempfile.TemporaryDirectory(prefix="ai-workspace-stt-") as directory:
            audio_path = Path(directory) / "input.audio"
            await asyncio.to_thread(audio_path.write_bytes, audio)
            audio_path.chmod(0o600)
            process = await asyncio.create_subprocess_exec(
                str(self.python),
                str(self.worker),
                "--model-directory",
                str(self.model_directory),
                "--audio",
                str(audio_path),
                "--device",
                self.device,
                "--compute-type",
                self.compute_type,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._environment(),
            )
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    if process.stdout is None:  # pragma: no cover - subprocess contract
                        raise SpeechRuntimeUnavailableError(
                            "speech recognition runtime is unavailable"
                        )
                    stdout = await self._read_bounded_stdout(process.stdout)
                    await process.wait()
            except BaseException:
                await self._stop(process)
                raise
        if process.returncode != 0 or len(stdout) > MAX_STT_RESPONSE_BYTES:
            raise SpeechRuntimeUnavailableError(
                "speech recognition runtime is unavailable"
            )
        try:
            payload: Any = json.loads(stdout)
            if not isinstance(payload, dict) or set(payload) != {
                "text",
                "language",
                "duration_seconds",
            }:
                raise ValueError
            return TranscriptionResult(
                text=payload["text"],
                language=payload["language"],
                duration_seconds=payload["duration_seconds"],
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SpeechRuntimeUnavailableError(
                "speech recognition output is unavailable"
            ) from exc

    @staticmethod
    async def _read_bounded_stdout(reader: asyncio.StreamReader) -> bytes:
        output = bytearray()
        while True:
            chunk = await reader.read(min(8_192, MAX_STT_RESPONSE_BYTES + 1))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > MAX_STT_RESPONSE_BYTES:
                raise SpeechRuntimeUnavailableError(
                    "speech recognition output exceeded its bound"
                )

    def _environment(self) -> dict[str, str]:
        environment = {
            "PATH": str(self.python.parent),
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "LC_ALL": "C.UTF-8",
        }
        if self.library_directories:
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                str(path) for path in self.library_directories
            )
        return environment

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            async with asyncio.timeout(2.0):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
