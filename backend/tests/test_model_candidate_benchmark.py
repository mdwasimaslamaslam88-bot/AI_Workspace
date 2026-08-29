import hashlib
import json
import subprocess

import scripts.model_candidate_benchmark as candidate_benchmark
from scripts.model_candidate_benchmark import (
    BASELINE_PROFILE,
    BASELINE_MODEL_REFERENCES,
    CODE_GENERATION_PROFILE,
    CODE_GENERATION_REQUIREMENTS_PROFILE,
    CODING_REQUIREMENTS_PROFILE,
    DEBUGGING_REQUIREMENTS_PROFILE,
    DEBUGGING_PROFILE,
    CURRENT_HARDWARE_DISCOVERY_REFERENCES,
    CURRENT_HARDWARE_VISION_REFERENCES,
    MODEL_REFERENCES,
    QWEN3_THINKING_AUTO_PROFILE,
    VISION_PROFILE,
    _percentile,
    build_comparison_cases,
    comparison_cases_for_profile,
    CandidateBenchmarkRunner,
    _installed_model_blob_verification,
    _routing_decision,
)


def test_comparison_matrix_covers_complete_requested_categories():
    cases = build_comparison_cases()
    counts = {}
    for item in cases:
        counts[item.category] = counts.get(item.category, 0) + 1

    assert counts == {
        "coding": 20,
        "debugging": 20,
        "reasoning": 50,
        "mathematics": 51,
        "expert_analysis": 12,
        "long_context": 10,
        "exact_output": 34,
    }
    assert len(cases) == 197


def test_exact_output_matrix_preserves_known_terminology_and_recovery_cases():
    cases = build_comparison_cases()
    exact = {item.case.test_id: item.case for item in cases if item.category == "exact_output"}

    assert exact["exact-output-base-case"].exact == "base case"
    assert exact["exact-output-recovered"].exact == "RECOVERED"


def test_thinking_auto_profile_runs_complete_exact_output_category():
    cases = comparison_cases_for_profile(QWEN3_THINKING_AUTO_PROFILE)

    assert len(cases) == 34
    assert {item.category for item in cases} == {"exact_output"}
    assert comparison_cases_for_profile(BASELINE_PROFILE) == build_comparison_cases()
    assert comparison_cases_for_profile(VISION_PROFILE) == ()


def test_code_generation_profiles_skip_the_unrelated_text_matrix():
    assert comparison_cases_for_profile(CODE_GENERATION_PROFILE) == ()
    assert comparison_cases_for_profile(CODE_GENERATION_REQUIREMENTS_PROFILE) == ()


def test_coding_requirements_profile_runs_only_the_complete_coding_category():
    cases = comparison_cases_for_profile(CODING_REQUIREMENTS_PROFILE)

    assert len(cases) == 20
    assert {item.category for item in cases} == {"coding"}


def test_debugging_requirements_profile_runs_only_complete_debugging_category():
    cases = comparison_cases_for_profile(DEBUGGING_REQUIREMENTS_PROFILE)

    assert len(cases) == 20
    assert {item.category for item in cases} == {"debugging"}
    assert comparison_cases_for_profile(DEBUGGING_PROFILE) == cases


def test_candidate_models_are_exact_fixed_local_references():
    assert BASELINE_MODEL_REFERENCES == (
        "qwen3:8b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:14b-instruct-q3_K_L",
    )
    assert CURRENT_HARDWARE_DISCOVERY_REFERENCES == (
        "qwen3:14b-q4_K_M",
        "deepcoder:14b-preview-q4_K_M",
        "gemma4:12b-it-q4_K_M",
        "qwen3.5:9b-q4_K_M",
        "ministral-3:14b-instruct-2512-q4_K_M",
        "phi4:14b-q4_K_M",
    )
    assert MODEL_REFERENCES == (
        *BASELINE_MODEL_REFERENCES,
        *CURRENT_HARDWARE_DISCOVERY_REFERENCES,
        "qwen2.5vl:7b",
    )
    assert "phi4-reasoning:14b-q4_K_M" not in MODEL_REFERENCES


def test_percentile_is_bounded_and_deterministic():
    assert _percentile([], 0.95) == 0.0
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0.0) == 1.0
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0


def test_zero_keep_alive_resource_summary_does_not_report_zero_model_vram():
    runner = object.__new__(CandidateBenchmarkRunner)
    runner.results = [
        {
            "comparison_category": "exact_output",
            "result": "PASS",
            "score": 100.0,
            "latency_seconds": 1.0,
            "actual_answer": "OK",
            "request_error": None,
        }
    ]
    runner.resource_samples = [
        {
            "gpu": {
                "name": "Synthetic GPU",
                "total_mib": 12_288,
                "used_mib": 8_500,
                "free_mib": 3_788,
                "temperature_c": 70,
                "power_watts": 120.0,
            },
            "ram": {
                "total_bytes": 80 * 1024**3,
                "used_bytes": 8 * 1024**3,
                "available_bytes": 72 * 1024**3,
            },
            "ollama_model": None,
        }
    ]

    resources = runner._summary()["resources"]

    assert resources["peak_gpu_used_mib"] == 8_500
    assert resources["peak_model_vram_bytes"] is None
    assert resources["ollama_process_visible_samples"] == 0
    assert "zero-second keep-alive" in resources["ollama_process_telemetry_note"]


def test_installed_model_verification_hashes_every_manifest_layer(
    tmp_path, monkeypatch
):
    model_root = tmp_path / "models"
    blob_root = model_root / "blobs"
    manifest_path = (
        model_root
        / "manifests"
        / "registry.ollama.ai"
        / "library"
        / "synthetic"
        / "vision-q4"
    )
    blob_root.mkdir(parents=True)
    manifest_path.parent.mkdir(parents=True)
    layer_contents = (b"model weights", b"vision projector")
    layers = []
    for media_type, content in zip(
        (
            "application/vnd.ollama.image.model",
            "application/vnd.ollama.image.projector",
        ),
        layer_contents,
        strict=True,
    ):
        digest = hashlib.sha256(content).hexdigest()
        (blob_root / f"sha256-{digest}").write_bytes(content)
        layers.append(
            {
                "mediaType": media_type,
                "digest": f"sha256:{digest}",
                "size": len(content),
            }
        )
    manifest_path.write_text(json.dumps({"layers": layers}), encoding="utf-8")
    monkeypatch.setattr(
        candidate_benchmark.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"FROM {blob_root / ('sha256-' + layers[0]['digest'][7:])}\n",
            stderr="",
        ),
    )

    result = _installed_model_blob_verification("synthetic:vision-q4")

    assert result["verified"] is True
    assert result["all_manifest_layers_verified"] is True
    assert result["manifest_layer_count"] == 2
    assert all(layer["verified"] is True for layer in result["manifest_layers"])
    assert str(tmp_path) not in json.dumps(result)


def test_routing_decision_does_not_overstate_latency_or_long_context_route():
    decision = _routing_decision()

    assert decision["candidate_production_admission"] == "rejected"
    assert "lower latency in some" in decision["candidate_reason"]
    assert "slower than" not in decision["candidate_reason"]
    assert "long_context" not in decision["unchanged_routes"]
    assert "hardware-aware catalog route" in decision["long_context_note"]
