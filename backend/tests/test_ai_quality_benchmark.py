from collections import Counter
from pathlib import Path

import httpx

from app.schemas.document import DocumentSearchQuery
from app.services.vision_input import _valid_png
from scripts.real_vision_smoke import _PNG as REAL_VISION_SMOKE_PNG
from scripts.ai_benchmark_cases import BenchmarkCase, build_text_matrix, validate_matrix
from scripts.ai_quality_benchmark import (
    DOCUMENT_SEARCH_LIMIT,
    MALFORMED_PNG,
    BenchmarkRunner,
    _bounded_judge_image_command,
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


def test_evaluator_separates_semantic_correctness_from_minor_formatting():
    categorical = BenchmarkCase(
        test_id="semantic-category",
        category="simple_reasoning",
        difficulty="easy",
        prompt="Yes or no only.",
        expected_behavior="Return yes.",
        exact="yes",
    )
    numeric = BenchmarkCase(
        test_id="semantic-number",
        category="multi_step_reasoning",
        difficulty="medium",
        prompt="Compute the result.",
        expected_behavior="Return 90.",
        exact="90",
    )

    categorical_result = _evaluate_answer(categorical, "Yes.", 0.5)
    numeric_result = _evaluate_answer(numeric, "Final value: 90", 0.5)

    assert categorical_result["result"] == "PASS"
    assert categorical_result["dimensions"]["correctness"] == 100
    assert categorical_result["dimensions"]["instruction_following"] == 70
    assert categorical_result["failure_reason"] == "literal_format:yes"
    assert numeric_result["result"] == "PASS"
    assert numeric_result["hallucination"] is False


def test_evaluator_keeps_genuinely_wrong_numeric_answer_failing():
    case = BenchmarkCase(
        test_id="wrong-number",
        category="arithmetic",
        difficulty="easy",
        prompt="Compute the result.",
        expected_behavior="Return 36.",
        exact="36",
    )

    result = _evaluate_answer(case, "42", 0.5)

    assert result["result"] == "FAIL"
    assert result["failure_reason"] == "exact:36"


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


def test_malformed_png_fixture_reaches_structural_validation():
    assert MALFORMED_PNG.startswith(b"\x89PNG\r\n\x1a\n")
    assert not _valid_png(MALFORMED_PNG)


def test_real_vision_smoke_fixture_passes_structural_validation():
    assert _valid_png(REAL_VISION_SMOKE_PNG)


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


def test_image_judge_copy_command_is_bounded_jpeg():
    command = _bounded_judge_image_command(
        Path("/synthetic/source.png"),
        Path("/synthetic/judge.jpg"),
    )

    assert command[-5:] == [
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "/synthetic/judge.jpg",
    ]
    assert "scale=384:384:force_original_aspect_ratio=decrease" in command[-6]


def test_multimodal_case_attaches_image_to_the_generated_user_message():
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.owner_token = "synthetic-test-bearer"
    runner.model_ids = {"vision": "vision-model-id"}
    runner.conversation_ids = []
    requests: list[tuple[str, str, dict]] = []

    def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if path == "/api/v1/conversations":
            response = httpx.Response(
                201,
                json={"id": "conversation-id"},
                request=httpx.Request(method, "http://benchmark.invalid" + path),
            )
            return response, 0.1
        response = httpx.Response(
            201,
            json={"message": {"content": "BLUE", "citations": []}},
            request=httpx.Request(method, "http://benchmark.invalid" + path),
        )
        return response, 0.2

    runner._request = request
    case = BenchmarkCase(
        test_id="vision",
        category="vision",
        difficulty="hard",
        prompt="Inspect the synthetic image.",
        expected_behavior="Use the attached image.",
        model_role="vision",
    )

    answer, latency, citations, model_id = runner._generate_case(
        case,
        attachments=["asset-id"],
        model_role="vision",
    )

    assert (answer, latency, citations, model_id) == (
        "BLUE",
        0.2,
        [],
        "vision-model-id",
    )
    create_body = requests[0][2]["json"]
    generation_body = requests[1][2]["json"]
    assert "attachment_ids" not in create_body
    assert create_body["initial_message"] != case.prompt
    assert generation_body["user_message"] == case.prompt
    assert generation_body["attachment_ids"] == ["asset-id"]


def test_image_judge_uses_a_disposable_owner_upload_copy():
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    runner.synthetic_asset_ids = []
    observed: dict[str, object] = {}
    runner._download_asset = lambda asset_id: (
        httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nsynthetic",
            request=httpx.Request("GET", "http://benchmark.invalid/source"),
        ),
        0.1,
    )
    runner._bounded_judge_copy = lambda content: b"\xff\xd8\xffsynthetic-jpeg"

    def upload(filename, content, media_type):
        observed["upload"] = (filename, content, media_type)
        return (
            httpx.Response(
                201,
                json={"id": "judge-copy-id"},
                request=httpx.Request("POST", "http://benchmark.invalid/assets"),
            ),
            0.2,
        )

    def generate(case, **kwargs):
        observed["attachments"] = kwargs["attachments"]
        return "red circle", 0.3, [], "vision-id"

    runner._upload = upload
    runner._generate_case = generate

    answer, evaluation, latency = runner._vision_judge_asset(
        "judge-case",
        "generated-asset-id",
        "Inspect the image.",
        ("red",),
    )

    assert answer == "red circle"
    assert evaluation["result"] == "PASS"
    assert round(latency, 4) == 0.6
    assert observed["attachments"] == ["judge-copy-id"]
    assert observed["upload"] == (
        "judge-case-judge-copy.jpg",
        b"\xff\xd8\xffsynthetic-jpeg",
        "image/jpeg",
    )
    assert runner.synthetic_asset_ids == ["judge-copy-id"]
