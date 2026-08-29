import pytest

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelDescriptor,
    ModelModality,
    ModelScaleClass,
)
from app.ai.routing import (
    InferenceMode,
    ModelRoutingUnavailableError,
    ModelTask,
    TaskAwareModelRouter,
    task_system_instruction,
)
from app.hardware.planner import GIBIBYTE


def _model(
    index: int,
    name: str,
    *,
    capabilities: tuple[ModelCapability, ...] = (
        ModelCapability.TEXT_GENERATION,
    ),
    scale: ModelScaleClass = ModelScaleClass.SEVEN_TO_EIGHT_B,
    context: int = 32_768,
    runnable: bool = True,
    available: ModelAvailability = ModelAvailability.AVAILABLE,
) -> ModelDescriptor:
    return ModelDescriptor(
        model_id=f"ollama:{index:024x}",
        display_name=name,
        runtime_id="ollama",
        modality=ModelModality.TEXT,
        family=name,
        parameter_class=scale.value,
        capabilities=capabilities,
        context_window=context,
        quantization="Q4_K_M",
        estimated_vram_bytes=5 * GIBIBYTE,
        availability=available,
        scale_class=scale,
        required_vram_bytes=6 * GIBIBYTE,
        required_ram_bytes=8 * GIBIBYTE,
        runnable_now=runnable,
        future_capable=not runnable,
    )


def test_router_uses_measured_task_profiles_on_current_hardware():
    capabilities = (
        ModelCapability.TEXT_GENERATION,
        ModelCapability.CODE,
    )
    qwen3 = _model(1, "Qwen3 8B", capabilities=capabilities, context=40_960)
    coder = _model(2, "Qwen2.5 Coder 7B", capabilities=capabilities)
    models = (qwen3, coder)
    router = TaskAwareModelRouter()

    code = router.select(models, ModelTask.CODE_GENERATION)
    exact = router.select(models, ModelTask.EXACT_OUTPUT)
    coding = router.select(models, ModelTask.CODING)

    assert code.model_id == qwen3.model_id
    assert code.inference_mode is InferenceMode.THINKING_DISABLED
    assert exact.model_id == qwen3.model_id
    assert exact.inference_mode is InferenceMode.THINKING_DISABLED
    assert coding.model_id == coder.model_id

    debugging = router.select(models, ModelTask.DEBUGGING)
    expert = router.select(models, ModelTask.EXPERT_ANALYSIS)
    assert debugging.model_id == qwen3.model_id
    assert expert.model_id == qwen3.model_id


def test_router_uses_code_capability_when_runtime_normalizes_coder_family():
    qwen3 = _model(1, "Qwen3 8B", context=40_960)
    normalized_coder = _model(
        2,
        "qwen2 7.6B",
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CODE,
        ),
    )
    models = (qwen3, normalized_coder)

    coding = TaskAwareModelRouter().select(models, ModelTask.CODING)
    exact = TaskAwareModelRouter().select(models, ModelTask.EXACT_OUTPUT)
    code_generation = TaskAwareModelRouter().select(
        models,
        ModelTask.CODE_GENERATION,
    )

    assert coding.model_id == normalized_coder.model_id
    assert exact.model_id == qwen3.model_id
    assert code_generation.model_id == qwen3.model_id


def test_router_limits_a_measured_preference_to_its_evidence_backed_task():
    qwen3 = _model(1, "Qwen3 8B", context=40_960)
    gemma = _model(
        2,
        "Gemma 4 12B",
        scale=ModelScaleClass.FOURTEEN_B,
    )
    router = TaskAwareModelRouter({ModelTask.CODE_GENERATION: gemma.model_id})

    code_generation = router.select(
        (qwen3, gemma),
        ModelTask.CODE_GENERATION,
    )
    reasoning = router.select((qwen3, gemma), ModelTask.REASONING)

    assert code_generation.model_id == gemma.model_id
    assert qwen3.model_id in code_generation.fallback_model_ids
    assert reasoning.model_id == qwen3.model_id
    assert gemma.model_id not in reasoning.fallback_model_ids


def test_router_falls_back_when_a_task_preference_is_not_runnable():
    qwen3 = _model(1, "Qwen3 8B", context=40_960)
    unavailable = _model(
        2,
        "Candidate 14B",
        scale=ModelScaleClass.FOURTEEN_B,
        runnable=False,
    )
    router = TaskAwareModelRouter(
        {ModelTask.CODE_GENERATION: unavailable.model_id}
    )

    decision = router.select(
        (qwen3, unavailable),
        ModelTask.CODE_GENERATION,
    )

    assert decision.model_id == qwen3.model_id


def test_router_requires_capability_context_and_live_hardware_admission():
    text = _model(1, "Qwen3 8B", context=40_960)
    vision = _model(
        2,
        "Qwen Vision 7B",
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        ),
        context=128_000,
    )
    unavailable = _model(
        3,
        "Unavailable Vision 70B",
        capabilities=(
            ModelCapability.TEXT_GENERATION,
            ModelCapability.VISION_INPUT,
        ),
        scale=ModelScaleClass.SEVENTY_B,
        runnable=False,
    )

    decision = TaskAwareModelRouter().select(
        (text, vision, unavailable),
        ModelTask.VISION,
        required_context_tokens=100_000,
    )

    assert decision.model_id == vision.model_id
    assert unavailable.model_id not in decision.fallback_model_ids


def test_router_admits_a_larger_future_model_without_hardware_name_checks():
    current = _model(1, "Qwen3 8B", context=40_960)
    future_installed = _model(
        2,
        "Independent Dense 70B",
        scale=ModelScaleClass.SEVENTY_B,
        context=128_000,
    )

    decision = TaskAwareModelRouter().select(
        (current, future_installed),
        ModelTask.REASONING,
    )

    assert decision.model_id == future_installed.model_id


def test_router_fails_closed_when_no_model_satisfies_task():
    with pytest.raises(ModelRoutingUnavailableError):
        TaskAwareModelRouter().select(
            (_model(1, "Text Only"),),
            ModelTask.IMAGE_GENERATION,
        )


def test_every_declared_task_has_a_capability_contract():
    all_capabilities = tuple(ModelCapability)
    universal = _model(
        1,
        "Synthetic Contract Model",
        capabilities=all_capabilities,
        context=1_000_000,
    )
    router = TaskAwareModelRouter()

    decisions = {
        task: router.select((universal,), task) for task in ModelTask
    }

    assert set(decisions) == set(ModelTask)
    assert all(item.model_id == universal.model_id for item in decisions.values())


def test_bounded_task_contracts_are_generic_and_do_not_contain_answers():
    exact = task_system_instruction(ModelTask.EXACT_OUTPUT)
    code = task_system_instruction(ModelTask.CODE_GENERATION)
    expert = task_system_instruction(ModelTask.EXPERT_ANALYSIS)

    assert exact is not None and "literal" in exact
    assert code is not None and "simulate every stated edge case" in code
    assert expert is not None and "explicitly named" in expert
    assert task_system_instruction(ModelTask.GENERAL_CHAT) is None
    assert all("RECOVERED" not in value for value in (exact, code, expert))
