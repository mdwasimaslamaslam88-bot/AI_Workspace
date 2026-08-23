import math

import pytest

from app.audio import MAX_TRANSCRIPT_CHARACTERS, TranscriptionResult


def test_transcription_result_accepts_bounded_local_metadata():
    result = TranscriptionResult("A private local transcript", "en", 1.25)

    assert result.text == "A private local transcript"
    assert result.language == "en"
    assert result.duration_seconds == 1.25


@pytest.mark.parametrize(
    "values",
    [
        {"text": ""},
        {"text": "x" * (MAX_TRANSCRIPT_CHARACTERS + 1)},
        {"text": "unsafe\x00transcript"},
        {"language": "unsafe/language"},
        {"duration_seconds": 0},
        {"duration_seconds": 601},
        {"duration_seconds": math.inf},
        {"duration_seconds": True},
    ],
)
def test_transcription_result_rejects_unbounded_or_unsafe_metadata(values):
    fields = {
        "text": "Safe transcript",
        "language": "en",
        "duration_seconds": 1.0,
    }
    fields.update(values)

    with pytest.raises(ValueError):
        TranscriptionResult(**fields)
