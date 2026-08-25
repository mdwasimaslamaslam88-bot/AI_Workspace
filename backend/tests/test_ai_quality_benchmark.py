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
    _baseline_initial_score,
    _bounded_judge_image_command,
    _bounded_noisy_audio_command,
    _evaluate_answer,
    _normalized_transcript_words,
    _refresh_text_record,
    _transcript_metrics,
)


def test_corrected_objective_cases_remove_ambiguous_oracles():
    cases = {case.test_id: case for case in build_text_matrix()}

    assert "nested object coordinates" in cases["medium-structured_data-09"].prompt
    assert "end of Friday" in cases["hard-cross_document_reasoning-04"].prompt
    assert "increment by one" in cases["expert-systems_reasoning-07"].prompt


def test_javascript_loose_equality_is_a_valid_comparison_fix():
    case = next(
        item
        for item in build_text_matrix()
        if item.test_id == "medium-debugging-02"
    )

    evaluation = _evaluate_answer(
        case,
        "if (x == 5) console.log('yes');",
        0.5,
    )

    assert evaluation["result"] == "PASS"


def test_numeric_words_satisfy_object_count_without_changing_raw_answer():
    case = BenchmarkCase(
        test_id="image-count",
        category="image_task",
        difficulty="expert",
        prompt="Inspect.",
        expected_behavior="Count objects.",
        required=("3", "blue"),
    )
    answer = "Exactly three blue cubes appear in one row."

    evaluation = _evaluate_answer(case, answer, 0.5)

    assert evaluation["result"] == "PASS"
    assert answer == "Exactly three blue cubes appear in one row."


def test_transcript_metrics_treat_spoken_and_numeric_47_as_equivalent():
    reference = "Pause, then continue: quartz harbor; value forty seven."
    transcript = "Pause, then continue, Quartz Harbor. Value 47."

    assert {"forty", "seven"} <= set(_normalized_transcript_words(transcript))
    metrics = _transcript_metrics(reference, transcript)
    assert metrics["wer"] == 0
    assert metrics["cer"] == 0


def test_semantic_math_accepts_equivalent_decimal_and_final_fraction():
    cases = {case.test_id: case for case in build_text_matrix()}

    decimal = _evaluate_answer(
        cases["expert-statistics_reasoning-07"],
        "Z-score: 2.0",
        0.5,
    )
    fraction = _evaluate_answer(
        cases["expert-probability_reasoning-10"],
        "4/10 simplifies to 2/5.",
        0.5,
    )

    assert decimal["result"] == "PASS"
    assert decimal["dimensions"]["instruction_following"] == 70
    assert fraction["result"] == "PASS"
    assert fraction["dimensions"]["instruction_following"] == 70


def test_dijkstra_auxiliary_space_is_an_explicit_case_scoped_alias():
    case = next(
        item
        for item in build_text_matrix()
        if item.test_id == "expert-advanced_algorithms-07"
    )
    evaluation = _evaluate_answer(
        case,
        "Time O(E log V); auxiliary space O(V).",
        0.5,
    )

    assert evaluation["result"] == "PASS"


def test_text_matrix_meets_every_required_easy_medium_and_hard_minimum():
    cases = build_text_matrix()
    validate_matrix(cases)
    counts = Counter(case.category for case in cases)

    assert len(cases) == 284
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
    assert all(
        counts[category] == 10
        for category in (
            "algebra_reasoning",
            "probability_reasoning",
            "statistics_reasoning",
            "discrete_math",
            "algorithm_reasoning",
            "systems_reasoning",
            "security_reasoning",
            "contradiction_detection",
        )
    )


def test_strict_oracle_prompts_state_the_operation_and_exact_format():
    cases = {case.test_id: case for case in build_text_matrix()}

    assert "revised phrase" in cases["easy-rewriting-08"].prompt
    assert "comma and one space" in cases["easy-instruction_following-02"].prompt
    assert "terminating concept" in cases["medium-debugging-10"].prompt
    assert cases["expert-discrete_math-10"].prompt.startswith("How many")


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


def test_evaluator_accepts_explicit_semantic_answers_but_keeps_format_penalty():
    yes_no = BenchmarkCase(
        test_id="semantic-yes-no",
        category="systems_reasoning",
        difficulty="expert",
        prompt="Yes or no only.",
        expected_behavior="Return no.",
        exact="no",
    )
    algorithm = BenchmarkCase(
        test_id="semantic-algorithm",
        category="algorithm_reasoning",
        difficulty="expert",
        prompt="BFS or DFS only.",
        expected_behavior="Return BFS.",
        exact="BFS",
    )

    yes_no_result = _evaluate_answer(yes_no, "No. Replicas can diverge.", 0.5)
    algorithm_result = _evaluate_answer(
        algorithm,
        "DFS is not shortest-path safe.\nAnswer: **BFS** is correct.",
        0.5,
    )

    assert yes_no_result["result"] == "PASS"
    assert yes_no_result["dimensions"]["instruction_following"] == 70
    assert algorithm_result["result"] == "PASS"
    assert algorithm_result["dimensions"]["instruction_following"] == 70


def test_evaluator_accepts_equivalent_complexity_notation():
    omega = BenchmarkCase(
        test_id="semantic-omega",
        category="algorithm_reasoning",
        difficulty="expert",
        prompt="Complexity only.",
        expected_behavior="Return the lower bound.",
        exact="Omega(n log n)",
    )
    cubic = BenchmarkCase(
        test_id="semantic-cubic",
        category="algorithm_reasoning",
        difficulty="expert",
        prompt="Complexity only.",
        expected_behavior="Return cubic time.",
        exact="O(n^3)",
    )

    assert _evaluate_answer(omega, "The bound is **\u03a9(n log n)**.", 0.5)["result"] == "PASS"
    assert _evaluate_answer(cubic, "O(V\u00b3)", 0.5)["result"] == "PASS"


def test_evaluator_accepts_numeric_sentence_and_labelled_numeric_tuple():
    numeric = BenchmarkCase(
        test_id="semantic-number-period",
        category="algebra_reasoning",
        difficulty="expert",
        prompt="Return the integer.",
        expected_behavior="Return 12.",
        exact="12",
    )
    stationary = BenchmarkCase(
        test_id="semantic-tuple",
        category="complex_math",
        difficulty="expert",
        prompt="Return p0,p1.",
        expected_behavior="Return stationary probabilities.",
        required=("0.6,0.4",),
    )

    assert _evaluate_answer(numeric, "The middle integer is 12.", 0.5)["result"] == "PASS"
    assert _evaluate_answer(
        numeric,
        "Intermediate value: 10. Answer: **12** (or twelve).",
        0.5,
    )["result"] == "PASS"
    assert _evaluate_answer(
        numeric,
        "Intermediate value: 10. **Answer:** 12 (or twelve).",
        0.5,
    )["result"] == "PASS"
    assert _evaluate_answer(stationary, "p0=0.6,p1=0.4", 0.5)["result"] == "PASS"


def test_evaluator_required_aliases_are_explicit_and_case_scoped():
    case = BenchmarkCase(
        test_id="semantic-summary",
        category="summarization",
        difficulty="easy",
        prompt="Summarize.",
        expected_behavior="Preserve the checkpoint.",
        required=("without downtime",),
        metadata={"required_aliases": {"without downtime": ("no downtime",)}},
    )

    assert _evaluate_answer(case, "Migration completed with no downtime.", 0.5)["result"] == "PASS"
    assert _evaluate_answer(case, "Migration completed.", 0.5)["result"] == "FAIL"


def test_evaluator_case_scoped_semantic_exact_regex_keeps_literal_penalty():
    case = BenchmarkCase(
        test_id="semantic-rewrite",
        category="rewriting",
        difficulty="easy",
        prompt="Rewrite as a polite request.",
        expected_behavior="Return a polite request.",
        exact="Please send the report.",
        metadata={
            "semantic_exact_regex": (r"(?is)\bplease\b.*\bsend\s+the\s+report\b",)
        },
    )

    result = _evaluate_answer(case, "Could you please send the report?", 0.5)

    assert result["result"] == "PASS"
    assert result["dimensions"]["correctness"] == 100
    assert result["dimensions"]["instruction_following"] == 70


def test_checkpoint_refresh_rescores_raw_answer_and_retry_without_replacing_them():
    case = BenchmarkCase(
        test_id="refresh",
        category="systems_reasoning",
        difficulty="expert",
        prompt="Yes or no only.",
        expected_behavior="Return no.",
        exact="no",
    )
    record = {
        "actual_answer": "No. Replicas may diverge.",
        "latency_seconds": 0.5,
        "score": 0.0,
        "result": "FAIL",
        "dimensions": {},
        "failure_reason": "exact:no",
        "hallucination": False,
        "safety_failure": False,
        "metadata": {},
        "retry_result": {
            "identical": {
                "actual_answer": "No. Replicas may diverge.",
                "latency_seconds": 0.5,
                "score": 0.0,
                "result": "FAIL",
            },
            "diagnostic_variant": {
                "actual_answer": "no",
                "latency_seconds": 0.5,
                "score": 0.0,
                "result": "FAIL",
            },
            "deterministic_failure": True,
        },
    }

    _refresh_text_record(case, record)

    assert record["actual_answer"] == "No. Replicas may diverge."
    assert record["result"] == "PASS"
    assert record["retry_result"]["identical"]["result"] == "PASS"
    assert record["retry_result"]["diagnostic_variant"]["result"] == "PASS"
    assert record["retry_result"]["deterministic_failure"] is False


def test_repeated_report_preserves_original_improvement_baseline():
    assert _baseline_initial_score(
        {"initial_score": 84.57, "total_score": 93.38}
    ) == 84.57
    assert _baseline_initial_score({"total_score": 84.57}) == 84.57
    assert _baseline_initial_score(None) is None


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
