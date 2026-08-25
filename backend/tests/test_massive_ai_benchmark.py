import json

import httpx

from scripts.massive_ai_benchmark import (
    Aggregate,
    MAX_LATENCY_SAMPLES,
    _interaction,
    _publish_summary,
    _tier,
    _validate_response,
)


def _response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "http://benchmark.invalid"),
    )


def test_massive_case_generation_is_deterministic_and_diverse():
    first = [_interaction(20260825, index) for index in range(40)]
    second = [_interaction(20260825, index) for index in range(40)]

    assert first == second
    assert {item.kind for item in first} == {
        "calculator",
        "conversation_turn",
        "tool_registry",
        "unauthorized_access",
        "unsafe_expression_rejection",
    }
    assert all("password" not in str(item.body).casefold() for item in first)


def test_massive_response_validation_is_objective():
    calculator = _interaction(20260825, 4)
    passed, reason = _validate_response(
        calculator,
        _response(201, {"status": "completed", "result": calculator.expected_value}),
    )
    wrong, wrong_reason = _validate_response(
        calculator,
        _response(201, {"status": "completed", "result": {"value": -1}}),
    )

    assert (passed, reason) == (True, None)
    assert (wrong, wrong_reason) == (False, "objective_response_mismatch")


def test_massive_aggregate_memory_is_bounded():
    aggregate = Aggregate(seed=7, total_target=MAX_LATENCY_SAMPLES + 500)
    for index in range(MAX_LATENCY_SAMPLES + 500):
        aggregate.record(
            _interaction(7, index),
            passed=True,
            latency=index / 1000,
            reason=None,
        )

    assert aggregate.completed == MAX_LATENCY_SAMPLES + 500
    assert len(aggregate.latency_samples) == MAX_LATENCY_SAMPLES
    assert aggregate.document()["pass_rate"] == 100.0


def test_massive_tiers_match_declared_pyramid():
    assert _tier(10_000) == "A"
    assert _tier(100_000) == "B"
    assert _tier(1_000_000) == "C"
    assert _tier(10_000_000) == "D"
    assert _tier(100_000_000) == "E"


def test_smoke_run_does_not_replace_larger_completed_report(tmp_path):
    report_path = tmp_path / "massive-run-summary.json"
    larger = {
        "status": "COMPLETE",
        "disposable_database": True,
        "production_data_modified": False,
        "failed": 0,
        "completed_interactions": 1_000_000,
        "maximum_supported_interactions": 100_000_000,
    }
    smaller = {
        **larger,
        "completed_interactions": 10_000,
    }

    assert _publish_summary(report_path, larger) is True
    assert _publish_summary(report_path, smaller) is False
    assert json.loads(report_path.read_text()) == larger


def test_larger_completed_report_replaces_smaller_report(tmp_path):
    report_path = tmp_path / "massive-run-summary.json"
    smaller = {
        "status": "COMPLETE",
        "disposable_database": True,
        "production_data_modified": False,
        "failed": 0,
        "completed_interactions": 10_000,
        "maximum_supported_interactions": 100_000_000,
    }
    larger = {
        **smaller,
        "completed_interactions": 1_000_000,
    }

    assert _publish_summary(report_path, smaller) is True
    assert _publish_summary(report_path, larger) is True
    assert json.loads(report_path.read_text()) == larger
