from scripts.model_candidate_benchmark import (
    BASELINE_PROFILE,
    MODEL_REFERENCES,
    QWEN3_THINKING_AUTO_PROFILE,
    _percentile,
    build_comparison_cases,
    comparison_cases_for_profile,
    CandidateBenchmarkRunner,
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


def test_candidate_models_are_exact_fixed_local_references():
    assert MODEL_REFERENCES == (
        "qwen3:8b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:14b-instruct-q3_K_L",
    )


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
