from scripts.current_hardware_model_discovery import (
    CURRENT_CATEGORY_BASELINES,
    EXPECTED_CATEGORIES,
    OFFICIAL_MANIFEST_RESIDENT_BYTES,
    _matrix_fingerprint,
    _rank_categories,
    _required_vram_bytes,
    _apply_task_preferences_to_routes,
    _upgrade_experiment_document,
)
from app.ai.catalog import public_model_id


def test_phi4_manifest_is_rejected_by_twelve_gibibyte_reserve_contract():
    model_bytes = OFFICIAL_MANIFEST_RESIDENT_BYTES["phi4-reasoning:14b-q4_K_M"]

    assert _required_vram_bytes(model_bytes) > 12_288 * 1024**2


def test_admitted_manifest_sizes_fit_twelve_gibibyte_reserve_contract():
    for reference, model_bytes in OFFICIAL_MANIFEST_RESIDENT_BYTES.items():
        if reference.startswith("phi4-reasoning:"):
            continue
        assert _required_vram_bytes(model_bytes) <= 12_288 * 1024**2


def test_matrix_fingerprint_does_not_depend_on_model_answer():
    first = {
        "results": [
            {
                "test_id": "case-1",
                "comparison_category": "reasoning",
                "prompt": "Prompt",
                "expected_behavior": "Expected",
                "actual_answer": "first",
            }
        ]
    }
    second = {
        "results": [{**first["results"][0], "actual_answer": "second"}]
    }

    assert _matrix_fingerprint(first) == _matrix_fingerprint(second)


def test_complete_category_route_requires_material_nonregressing_candidate():
    category = "reasoning"
    current_reference = CURRENT_CATEGORY_BASELINES[category]
    reports = []
    for reference, score, passed in (
        (current_reference, 94.0, 48),
        ("qwen2.5-coder:7b", 80.0, 40),
        ("qwen2.5-coder:14b-instruct-q3_K_L", 84.0, 42),
        ("qwen3:14b-q4_K_M", 97.0, 49),
        ("deepcoder:14b-preview-q4_K_M", 86.0, 43),
        ("gemma4:12b-it-q4_K_M", 90.0, 45),
        ("qwen3.5:9b-q4_K_M", 95.0, 48),
        ("ministral-3:14b-instruct-2512-q4_K_M", 91.0, 46),
    ):
        reports.append(
            {
                "model_reference": reference,
                "summary": {
                    "categories": {
                        candidate_category: {
                            "tests": 50,
                            "pass": passed,
                            "partial": 0,
                            "fail": 50 - passed,
                            "score": score,
                            "average_latency_seconds": 5.0,
                            "p95_latency_seconds": 6.0,
                        }
                        for candidate_category in EXPECTED_CATEGORIES
                    },
                    "stability": {
                        "request_failures": 0,
                        "thermal_guard_triggered": False,
                        "vram_guard_triggered": False,
                        "ram_guard_triggered": False,
                    },
                },
            }
        )

    result = _rank_categories(reports)[category]

    assert result["winner"] == "qwen3:14b-q4_K_M"
    assert result["material_route_improvement"] is True
    assert result["route_recommendation"] == "qwen3:14b-q4_K_M"


def test_upgrade_summary_records_only_applied_evidence_backed_routes():
    aggregate = {
        "benchmark_commit": "a" * 40,
        "models": [
            {
                "model_reference": "candidate:12b",
                "model_id": "ollama-local:" + "b" * 24,
                "model_metadata": {"parameter_class": "12B"},
                "summary": {"tests": 221, "score": 90.0},
            }
        ],
        "category_results": {
            "executable_code_generation": {
                "winner": "candidate:12b",
                "ranking": [],
                "route_recommendation": "candidate:12b",
            },
            "reasoning": {
                "winner": "current:8b",
                "ranking": [],
                "route_recommendation": "current:8b",
            },
        },
        "installation_verification": {
            "candidate:12b": {"verified": True}
        },
        "routing_recommendation": {
            "changes": {
                "executable_code_generation": "candidate:12b"
            },
            "long_context_note": "unchanged",
        },
    }

    summary = _upgrade_experiment_document(
        aggregate,
        production_allowlist=("candidate:12b",),
        task_model_preferences={"code_generation": "candidate:12b"},
    )

    assert summary["routing_decision"]["candidate_production_admission"] == (
        "admitted_for_evidence_backed_tasks"
    )
    assert summary["routing_decision"]["applied_route_changes"] == {
        "executable_code_generation": "candidate:12b"
    }
    assert summary["models"][0]["production_routed"] is True


def test_report_route_sync_reserves_candidate_for_configured_task_only():
    candidate_reference = "candidate:12b"
    candidate_id = public_model_id("ollama-local", candidate_reference)
    current_id = public_model_id("ollama-local", "current:8b")
    routes = [
        {
            "task": "reasoning",
            "model_id": candidate_id,
            "fallback_model_ids": [current_id],
        },
        {
            "task": "code_generation",
            "model_id": current_id,
            "fallback_model_ids": [candidate_id],
        },
    ]

    _apply_task_preferences_to_routes(
        routes,
        {"code_generation": candidate_reference},
    )

    assert routes[0]["model_id"] == current_id
    assert routes[0]["fallback_model_ids"] == []
    assert routes[1]["model_id"] == candidate_id
    assert routes[1]["fallback_model_ids"] == [current_id]
