from __future__ import annotations

import asyncio
from io import BytesIO
import math
from pathlib import Path
import tempfile
import unicodedata
import wave

from app.audio import SpeechRuntimeInputError, SpeechRuntimeUnavailableError


MAX_TTS_INPUT_CHARACTERS = 2_000
MAX_TTS_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_TTS_DURATION_SECONDS = 120.0
MAX_TTS_TIMEOUT_SECONDS = 180.0


def _require_local_file(path: Path, field_name: str) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{field_name} must be a regular file")
    return resolved


def _validate_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("speech synthesis text must be a string")
    if not text.strip() or len(text) > MAX_TTS_INPUT_CHARACTERS:
        raise SpeechRuntimeInputError("speech synthesis text is outside its bound")
    if any(
        unicodedata.category(character).startswith("C")
        and character not in {"\n", "\t"}
        for character in text
    ):
        raise SpeechRuntimeInputError("speech synthesis text contains control data")
    return text


def _validate_wav(data: bytes) -> None:
    if not data or len(data) > MAX_TTS_OUTPUT_BYTES:
        raise SpeechRuntimeUnavailableError("speech synthesis output is unavailable")
    try:
        with wave.open(BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
    except (EOFError, wave.Error) as exc:
        raise SpeechRuntimeUnavailableError(
            "speech synthesis output is unavailable"
        ) from exc
    duration = frame_count / sample_rate if sample_rate else math.inf
    if (
        channels not in {1, 2}
        or sample_width not in {1, 2, 3, 4}
        or not 8_000 <= sample_rate <= 48_000
        or not 0 < duration <= MAX_TTS_DURATION_SECONDS
    ):
        raise SpeechRuntimeUnavailableError("speech synthesis output is unavailable")


class PiperSpeechSynthesisRuntime:
    runtime_id = "piper"

    def __init__(
        self,
        binary: Path,
        model: Path,
        config: Path,
        *,
        model_reference: str,
        timeout_seconds: float = 60.0,
        max_active: int = 1,
    ) -> None:
        if not isinstance(model_reference, str) or not model_reference.strip():
            raise ValueError("Piper model reference must be nonblank")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_TTS_TIMEOUT_SECONDS
        ):
            raise ValueError("Piper timeout is outside its bound")
        if isinstance(max_active, bool) or not isinstance(max_active, int):
            raise TypeError("Piper concurrency must be an integer")
        if not 1 <= max_active <= 2:
            raise ValueError("Piper concurrency is outside its bound")
        self.binary = _require_local_file(binary, "Piper binary")
        self.model = _require_local_file(model, "Piper model")
        self.config = _require_local_file(config, "Piper config")
        self.model_reference = model_reference
        self.timeout_seconds = float(timeout_seconds)
        self._admission = asyncio.Semaphore(max_active)

    async def synthesize(self, text: str) -> bytes:
        validated = _validate_text(text)
        async with self._admission:
            return await self._synthesize_admitted(validated)

    async def _synthesize_admitted(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="ai-workspace-tts-") as directory:
            output_path = Path(directory) / "speech.wav"
            process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "--model",
                str(self.model),
                "--config",
                str(self.config),
                "--output_file",
                str(output_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={
                    "PATH": str(self.binary.parent),
                    "PYTHONNOUSERSITE": "1",
                    "LC_ALL": "C.UTF-8",
                },
            )
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    await process.communicate((text + "\n").encode("utf-8"))
            except BaseException:
                await self._stop(process)
                raise
            if process.returncode != 0:
                raise SpeechRuntimeUnavailableError(
                    "speech synthesis runtime is unavailable"
                )
            try:
                size = output_path.stat().st_size
                if not 0 < size <= MAX_TTS_OUTPUT_BYTES:
                    raise SpeechRuntimeUnavailableError(
                        "speech synthesis output is unavailable"
                    )
                data = await asyncio.to_thread(output_path.read_bytes)
            except OSError as exc:
                raise SpeechRuntimeUnavailableError(
                    "speech synthesis output is unavailable"
                ) from exc
        _validate_wav(data)
        return data

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
