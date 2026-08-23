import pytest

from scripts.real_voice_smoke import _contains_spoken_checkpoint


@pytest.mark.parametrize(
    "transcript",
    (
        "Testing local speech. The color is amber. The object is a lantern.",
        "Testing local speech, the color is gold, and the object is a lantern.",
        "Testing speech. The color is amber. The object is a lantern.",
    ),
)
def test_spoken_checkpoint_accepts_bounded_one_term_acoustic_variation(transcript):
    assert _contains_spoken_checkpoint(transcript)


@pytest.mark.parametrize(
    "transcript",
    (
        "Testing local speech.",
        "The color is amber and the object is a lantern.",
        "Testing local speech with an object and a lantern.",
        "Testing local speech with the color amber.",
    ),
)
def test_spoken_checkpoint_requires_all_clauses_and_five_expected_terms(transcript):
    assert not _contains_spoken_checkpoint(transcript)
