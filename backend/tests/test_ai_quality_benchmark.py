from collections import Counter
from pathlib import Path

from app.schemas.document import DocumentSearchQuery
from scripts.ai_benchmark_cases import BenchmarkCase, build_text_matrix, validate_matrix
from scripts.ai_quality_benchmark import (
    DOCUMENT_SEARCH_LIMIT,
    BenchmarkRunner,
    _bounded_noisy_audio_command,
    _evaluate_answer,
)


def test_text_matrix_meets_every_required_easy_medium_and_hard_minimum():
    cases = build_text_matrix()
    validate_matrix(cases)
    counts = Counter(case.category for case in cases)

    assert len(cases) == 204
    assert all(
        counts[category] == 10
        for category in (
            "factual",
            "simple_reasoning",
            "arithmetic",
            "summarization",
            "rewriting",
            "instruction_following",
            "multi_step_reasoning",
            "coding",
            "debugging",
            "structured_data",
            "comparison_decision",
            "context_following",
            "advanced_coding",
            "difficult_debugging",
            "complex_planning",
            "long_context_reasoning",
            "ambiguous_resolvable",
            "cross_document_reasoning",
        )
    )


def test_evaluator_preserves_strict_answer_and_scores_literal_failure():
    case = BenchmarkCase(
        test_id="strict",
        category="instruction_following",
        difficulty="easy",
        prompt="Reply exactly SAFE.",
        expected_behavior="Return SAFE only.",
        exact="SAFE",
    )

    passed = _evaluate_answer(case, "SAFE", 0.5)
    failed = _evaluate_answer(case, "The answer is SAFE.", 0.5)

    assert passed["result"] == "PASS"
    assert failed["result"] == "FAIL"
    assert failed["failure_reason"] == "exact:SAFE"


def test_evaluator_requires_exact_json_without_code_fence():
    case = BenchmarkCase(
        test_id="json",
        category="structured_data",
        difficulty="medium",
        prompt="Return JSON.",
        expected_behavior="Return the exact object.",
        expected_json={"ready": True},
    )

    assert _evaluate_answer(case, '{"ready":true}', 0.5)["result"] == "PASS"
    assert _evaluate_answer(case, '```json\n{"ready":true}\n```', 0.5)["result"] == "FAIL"


def test_model_selection_recognizes_redacted_public_qwen_coder_family():
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.model_ids = {}
    runner.models = [
        {
            "model_id": "general-id",
            "display_name": "qwen3 8.2B",
            "capabilities": ["text_generation"],
            "installed": True,
            "runnable_now": True,
        },
        {
            "model_id": "coder-id",
            "display_name": "qwen2 7.6B",
            "capabilities": ["text_generation"],
            "installed": True,
            "runnable_now": True,
        },
        {
            "model_id": "vision-id",
            "display_name": "qwen25vl 8.3B",
            "capabilities": ["text_generation", "vision_input"],
            "installed": True,
            "runnable_now": True,
        },
        {
            "model_id": "embedding-id",
            "display_name": "nomic-bert 137M",
            "capabilities": ["embeddings"],
            "installed": True,
            "runnable_now": True,
        },
    ]

    runner._select_models()

    assert runner.model_ids == {
        "general": "general-id",
        "coder": "coder-id",
        "vision": "vision-id",
        "embedding": "embedding-id",
    }


def test_benchmark_document_search_uses_the_product_bound():
    query = DocumentSearchQuery(query="synthetic checkpoint", limit=DOCUMENT_SEARCH_LIMIT)

    assert query.limit == 4


def test_noisy_audio_fixture_is_bounded_mono_pcm():
    command = _bounded_noisy_audio_command(
        Path("/synthetic/source.wav"),
        Path("/synthetic/noisy.wav"),
    )

    assert command[-9:] == [
        "-t",
        "12",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "/synthetic/noisy.wav",
    ]
