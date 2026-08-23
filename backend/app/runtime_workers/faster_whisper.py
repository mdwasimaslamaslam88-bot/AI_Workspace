from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys


# Executing this file directly places its directory first on sys.path. Because
# the worker filename intentionally describes its runtime, that would otherwise
# shadow the installed ``faster_whisper`` package with this module itself.
_WORKER_DIRECTORY = Path(__file__).resolve().parent
sys.path = [
    entry
    for entry in sys.path
    if Path(entry or ".").resolve() != _WORKER_DIRECTORY
]

from faster_whisper import WhisperModel


MAX_TRANSCRIPT_CHARACTERS = 8_000
MAX_AUDIO_DURATION_SECONDS = 600.0


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model-directory", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), required=True)
    parser.add_argument("--compute-type", required=True)
    arguments = parser.parse_args()

    model_directory = Path(arguments.model_directory).resolve(strict=True)
    audio_path = Path(arguments.audio).resolve(strict=True)
    if not model_directory.is_dir() or not audio_path.is_file():
        raise RuntimeError("local speech input is unavailable")
    model = WhisperModel(
        str(model_directory),
        device=arguments.device,
        compute_type=arguments.compute_type,
        local_files_only=True,
    )
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=3,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    parts: list[str] = []
    character_count = 0
    for segment in segments:
        value = segment.text.strip()
        if not value:
            continue
        additional = len(value) + (1 if parts else 0)
        if character_count + additional > MAX_TRANSCRIPT_CHARACTERS:
            raise RuntimeError("local transcript exceeded its bound")
        parts.append(value)
        character_count += additional
    transcript = " ".join(parts).strip()
    duration = float(info.duration)
    if (
        not transcript
        or not math.isfinite(duration)
        or not 0 < duration <= MAX_AUDIO_DURATION_SECONDS
    ):
        raise RuntimeError("local transcription output is invalid")
    print(
        json.dumps(
            {
                "text": transcript,
                "language": info.language,
                "duration_seconds": duration,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
