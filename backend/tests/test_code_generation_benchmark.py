from pathlib import Path

from scripts.code_generation_benchmark import (
    build_code_generation_cases,
    extract_generated_artifact,
    verify_generated_code,
)


REPOSITORY_ROOT = Path(__file__).parents[2]


def _cases():
    return {
        case.test_id: case
        for case in build_code_generation_cases(REPOSITORY_ROOT)
    }


def test_code_generation_matrix_covers_every_supported_language_twice():
    cases = build_code_generation_cases(REPOSITORY_ROOT)

    assert len(cases) == 12
    assert {case.language for case in cases} == {
        "bash",
        "javascript",
        "python",
        "rust",
        "sql",
        "typescript",
    }
    assert all(
        sum(item.language == case.language for item in cases) == 2
        for case in cases
    )
    assert {
        case.test_id: case.model_role
        for case in cases
        if case.model_role != "coder"
    } == {"codegen-python-merge-intervals": "general"}


def test_artifact_extraction_preserves_original_code_without_repair():
    answer = "Before\n```python\ndef add(a, b):\n    return a+b\n```\nAfter"

    assert extract_generated_artifact(answer, "python") == (
        "def add(a, b):\n    return a+b"
    )


def test_static_safety_rejects_generated_filesystem_access_before_execution():
    result = verify_generated_code(
        _cases()["codegen-python-clamp"],
        "import os\ndef clamp(value, low, high): return value",
    )

    assert result["passed"] is False
    assert result["static_safety_passed"] is False
    assert result["evidence"] == []


def test_python_artifact_is_compiled_and_executed_in_bounded_sandbox():
    result = verify_generated_code(
        _cases()["codegen-python-clamp"],
        """def clamp(value, low, high):
    if low > high:
        raise ValueError()
    return min(high, max(low, value))
""",
    )

    assert result["passed"] is True
    assert [item["exit_code"] for item in result["evidence"]] == [0, 0]
    assert result["evidence"][-1]["stdout"] == "PASS"


def test_sql_artifact_executes_against_disposable_in_memory_database():
    result = verify_generated_code(
        _cases()["codegen-sql-aggregation"],
        """SELECT customer, SUM(amount) AS total_amount
FROM sales
GROUP BY customer
HAVING SUM(amount) >= 10
ORDER BY total_amount DESC, customer ASC;""",
    )

    assert result["passed"] is True
    assert result["evidence"][-1]["stdout"] == "ada|12\ncy|12"


def test_verifier_evidence_redacts_disposable_filesystem_paths():
    result = verify_generated_code(
        _cases()["codegen-python-clamp"],
        "def clamp(:\n    pass",
    )

    serialized = str(result["evidence"])
    assert result["passed"] is False
    assert "/tmp/" not in serialized
