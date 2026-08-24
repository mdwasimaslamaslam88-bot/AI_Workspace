from collections import Counter

from scripts.ai_benchmark_cases import BenchmarkCase, build_text_matrix, validate_matrix
from scripts.ai_quality_benchmark import _evaluate_answer


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
