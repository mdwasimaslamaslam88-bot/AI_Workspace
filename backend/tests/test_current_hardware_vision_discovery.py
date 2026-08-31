import json
from pathlib import Path

from scripts.current_hardware_vision_discovery import (
    CURRENT_VISION_REFERENCE,
    _fingerprint,
    aggregate_vision_reports,
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


def test_vision_discovery_keeps_configured_embedding_model_allowlisted():
    repository_root = Path(__file__).resolve().parents[2]
    wrapper = (repository_root / "scripts/current_hardware_model_discovery.sh").read_text()

    assert 'OLLAMA_LOCAL_MODEL_ALLOWLIST="[\\"${reference}\\",\\"nomic-embed-text:latest\\"]"' in wrapper


def test_vision_discovery_excludes_nonbaseline_resource_guard_failure(
    tmp_path, monkeypatch
):
    references = (
        "qwen2.5vl:7b",
        "gemma4:12b-it-q4_K_M",
        "qwen3.5:9b-q4_K_M",
        "ministral-3:14b-instruct-2512-q4_K_M",
    )
    paths = []
    for index, reference in enumerate(references):
        path = tmp_path / f"report-{index}.json"
        if reference.startswith("ministral-3:"):
            report = {
                "run_status": "failed",
                "model_reference": reference,
                "failure": {"code": "gpu_thermal_guard"},
            }
        else:
            report = {
                "run_status": "complete",
                "model_reference": reference,
                "profile": {"id": "vision"},
                "summary": {
                    "tests": 7,
                    "categories": {
                        "vision": {
                            "tests": 7,
                            "pass": 7,
                            "partial": 0,
                            "fail": 0,
                            "score": 99.5,
                            "average_latency_seconds": index + 1.0,
                            "p95_latency_seconds": index + 2.0,
                        }
                    },
                    "stability": {"request_failures": 0},
                },
                "results": [
                    {
                        "test_id": "same",
                        "prompt": "same",
                        "expected_behavior": "same",
                    }
                ],
            }
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)

    result = aggregate_vision_reports(tmp_path, paths)

    assert len(result["models"]) == 3
    assert result["hardware_safety_rejections"][0]["model_reference"].startswith(
        "ministral-3:"
    )
