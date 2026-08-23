from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Protocol
import unicodedata


MAX_TRANSCRIPT_CHARACTERS = 8_000
MAX_AUDIO_DURATION_SECONDS = 600.0
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class SpeechRuntimeUnavailableError(RuntimeError):
    """A configured local speech runtime failed without exposing details."""


class SpeechRuntimeInputError(ValueError):
    """Speech input exceeded a fixed validation or resource bound."""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or len(self.text) > MAX_TRANSCRIPT_CHARACTERS
            or any(
                unicodedata.category(character).startswith("C")
                for character in self.text
            )
        ):
            raise ValueError("transcription text must be nonblank")
        if self.language is not None and (
            not isinstance(self.language, str)
            or _LANGUAGE_PATTERN.fullmatch(self.language) is None
        ):
            raise ValueError("transcription language is invalid")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or not 0 < self.duration_seconds <= MAX_AUDIO_DURATION_SECONDS
        ):
            raise ValueError("transcription duration must be positive and finite")


class SpeechRecognitionRuntime(Protocol):
    runtime_id: str
    model_reference: str

    async def transcribe(self, audio: bytes) -> TranscriptionResult: ...


class SpeechSynthesisRuntime(Protocol):
    runtime_id: str
    model_reference: str

    async def synthesize(self, text: str) -> bytes: ...
