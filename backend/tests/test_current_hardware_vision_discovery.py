import json

from scripts.current_hardware_vision_discovery import (
    CURRENT_VISION_REFERENCE,
    _fingerprint,
)


def test_vision_fingerprint_preserves_matrix_but_not_answer():
    first = {
        "results": [
            {
                "test_id": "vision-1",
                "prompt": "Read it",
                "expected_behavior": "Return the token",
                "actual_answer": "one",
            }
        ]
    }
    second = json.loads(json.dumps(first))
    second["results"][0]["actual_answer"] = "two"

    assert _fingerprint(first) == _fingerprint(second)


def test_existing_vision_model_is_explicit_comparison_baseline():
    assert CURRENT_VISION_REFERENCE == "qwen2.5vl:7b"
