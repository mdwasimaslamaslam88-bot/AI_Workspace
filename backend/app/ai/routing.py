from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelDescriptor,
    ModelScaleClass,
)


class ModelTask(StrEnum):
    GENERAL_CHAT = "general_chat"
    REASONING = "reasoning"
    MATHEMATICS = "mathematics"
    CODING = "coding"
    DEBUGGING = "debugging"
    CODE_GENERATION = "code_generation"
    EXPERT_ANALYSIS = "expert_analysis"
    VISION = "vision"
    RAG = "rag"
    MEMORY = "memory"
    SUMMARIZATION = "summarization"
    TOOL_CALLING = "tool_calling"
    WORKFLOW_PLANNING = "workflow_planning"
    LONG_CONTEXT = "long_context"
    EXACT_OUTPUT = "exact_output"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    VOICE_INPUT = "voice_input"
    VOICE_OUTPUT = "voice_output"


class InferenceMode(StrEnum):
    AUTO = "auto"
    THINKING_DISABLED = "thinking_disabled"


class ModelRoutingUnavailableError(RuntimeError):
    """No installed, available model safely satisfies a task contract."""


@dataclass(frozen=True, slots=True)
class ModelRoutingDecision:
    task: ModelTask
    model_id: str
    fallback_model_ids: tuple[str, ...]
    inference_mode: InferenceMode
    required_context_tokens: int


@dataclass(frozen=True, slots=True)
class ModelRoutingEvidence:
    quality_score: float
    median_latency_ms: float
    stability_rate: float

    def __post_init__(self) -> None:
        if not 0 <= self.quality_score <= 100:
            raise ValueError("routing quality score must be between 0 and 100")
        if self.median_latency_ms < 0:
            raise ValueError("routing latency must be non-negative")
        if not 0 <= self.stability_rate <= 1:
            raise ValueError("routing stability must be between 0 and 1")


_SCALE_QUALITY_RANK = {
    ModelScaleClass.SEVEN_TO_EIGHT_B: 1,
    ModelScaleClass.FOURTEEN_B: 2,
    ModelScaleClass.THIRTY_TO_THIRTY_FOUR_B: 3,
    ModelScaleClass.SEVENTY_B: 4,
    ModelScaleClass.HUNDRED_B_PLUS: 5,
    ModelScaleClass.TWO_HUNDRED_B_PLUS: 6,
    ModelScaleClass.FIVE_HUNDRED_B_PLUS: 7,
    ModelScaleClass.ONE_THOUSAND_B_PLUS: 8,
    ModelScaleClass.TWO_THOUSAND_B: 9,
    ModelScaleClass.MOE_VERY_LARGE: 9,
}


_REQUIRED_CAPABILITIES = {
    ModelTask.GENERAL_CHAT: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.REASONING: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.MATHEMATICS: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.CODING: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.DEBUGGING: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.CODE_GENERATION: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.EXPERT_ANALYSIS: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.VISION: frozenset(
        {ModelCapability.TEXT_GENERATION, ModelCapability.VISION_INPUT}
    ),
    ModelTask.RAG: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.MEMORY: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.SUMMARIZATION: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.TOOL_CALLING: frozenset(
        {ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_CALLING}
    ),
    ModelTask.WORKFLOW_PLANNING: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.LONG_CONTEXT: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.EXACT_OUTPUT: frozenset({ModelCapability.TEXT_GENERATION}),
    ModelTask.EMBEDDING: frozenset({ModelCapability.EMBEDDINGS}),
    ModelTask.IMAGE_GENERATION: frozenset({ModelCapability.IMAGE_GENERATION}),
    ModelTask.IMAGE_EDITING: frozenset({ModelCapability.IMAGE_EDITING}),
    ModelTask.VOICE_INPUT: frozenset({ModelCapability.SPEECH_RECOGNITION}),
    ModelTask.VOICE_OUTPUT: frozenset({ModelCapability.SPEECH_SYNTHESIS}),
}


_QUALITY_INTENSIVE_TASKS = frozenset(
    {
        ModelTask.REASONING,
        ModelTask.MATHEMATICS,
        ModelTask.CODING,
        ModelTask.DEBUGGING,
        ModelTask.CODE_GENERATION,
        ModelTask.EXPERT_ANALYSIS,
        ModelTask.RAG,
        ModelTask.MEMORY,
        ModelTask.WORKFLOW_PLANNING,
        ModelTask.LONG_CONTEXT,
    }
)


class TaskAwareModelRouter:
    """Choose only safe runnable models using task and hardware metadata.

    The router consumes public catalog descriptors, so replacing a GPU or
    installing a larger allowlisted model automatically changes eligibility
    without changing API, storage, RAG, memory, agent, or client contracts.
    """

    def __init__(
        self,
        preferred_model_ids: dict[ModelTask, str] | None = None,
        *,
        measured_evidence: dict[
            tuple[ModelTask, str], ModelRoutingEvidence
        ] | None = None,
    ) -> None:
        preferences = preferred_model_ids or {}
        if not isinstance(preferences, dict) or any(
            not isinstance(task, ModelTask)
            or not isinstance(model_id, str)
            or not model_id
            for task, model_id in preferences.items()
        ):
            raise TypeError(
                "preferred_model_ids must map ModelTask values to nonblank model IDs"
            )
        self.preferred_model_ids = dict(preferences)
        self.reserved_model_ids = frozenset(preferences.values())
        evidence = measured_evidence or {}
        if not isinstance(evidence, dict) or any(
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(key[0], ModelTask)
            or not isinstance(key[1], str)
            or not isinstance(value, ModelRoutingEvidence)
            for key, value in evidence.items()
        ):
            raise TypeError("measured_evidence must map task/model pairs to evidence")
        self.measured_evidence = dict(evidence)

    def select(
        self,
        models: tuple[ModelDescriptor, ...],
        task: ModelTask,
        *,
        required_context_tokens: int = 0,
    ) -> ModelRoutingDecision:
        if not isinstance(task, ModelTask):
            raise TypeError("task must be a ModelTask")
        if (
            isinstance(required_context_tokens, bool)
            or not isinstance(required_context_tokens, int)
            or not 0 <= required_context_tokens <= 10_000_000
        ):
            raise ValueError(
                "required_context_tokens must be between 0 and 10000000"
            )
        if not isinstance(models, tuple) or any(
            not isinstance(model, ModelDescriptor) for model in models
        ):
            raise TypeError("models must be a tuple of ModelDescriptor values")

        required = _REQUIRED_CAPABILITIES[task]
        eligible = [
            model
            for model in models
            if model.installed
            and model.runnable_now
            and model.availability is ModelAvailability.AVAILABLE
            and required.issubset(model.capabilities)
            and (
                required_context_tokens == 0
                or model.context_window is not None
                and model.context_window >= required_context_tokens
            )
        ]
        if not eligible:
            raise ModelRoutingUnavailableError(
                "no installed model satisfies the task and hardware contract"
            )

        preferred_model_id = self.preferred_model_ids.get(task)
        preferred = next(
            (
                model
                for model in eligible
                if model.model_id == preferred_model_id
            ),
            None,
        )
        unreserved = [
            model
            for model in eligible
            if model.model_id not in self.reserved_model_ids
        ]
        ranking_pool = (
            [preferred, *unreserved]
            if preferred is not None
            else unreserved or eligible
        )
        ranked = sorted(
            ranking_pool,
            key=lambda model: (
                model.model_id != preferred_model_id,
                -self._evidence_quality(model, task),
                -self._evidence_stability(model, task),
                -self._quality_score(model, task),
                self._evidence_latency(model, task),
                model.required_vram_bytes is None,
                model.required_vram_bytes or 0,
                model.model_id,
            ),
        )
        selected = ranked[0]
        return ModelRoutingDecision(
            task=task,
            model_id=selected.model_id,
            fallback_model_ids=tuple(model.model_id for model in ranked[1:]),
            inference_mode=(
                InferenceMode.THINKING_DISABLED
                if task in {ModelTask.CODE_GENERATION, ModelTask.EXACT_OUTPUT}
                else InferenceMode.AUTO
            ),
            required_context_tokens=required_context_tokens,
        )

    def _evidence_quality(self, model: ModelDescriptor, task: ModelTask) -> float:
        evidence = self.measured_evidence.get((task, model.model_id))
        return evidence.quality_score if evidence is not None else -1.0

    def _evidence_stability(self, model: ModelDescriptor, task: ModelTask) -> float:
        evidence = self.measured_evidence.get((task, model.model_id))
        return evidence.stability_rate if evidence is not None else -1.0

    def _evidence_latency(self, model: ModelDescriptor, task: ModelTask) -> float:
        evidence = self.measured_evidence.get((task, model.model_id))
        return evidence.median_latency_ms if evidence is not None else float("inf")

    @staticmethod
    def _quality_score(model: ModelDescriptor, task: ModelTask) -> int:
        family = (model.family or "").casefold()
        display_name = model.display_name.casefold()
        qwen3 = "qwen3" in family or "qwen3" in display_name
        coder = "coder" in family or "coder" in display_name
        score = 100

        if task in _QUALITY_INTENSIVE_TASKS:
            score += 25 * _SCALE_QUALITY_RANK.get(model.scale_class, 0)
        if task in {
            ModelTask.GENERAL_CHAT,
            ModelTask.REASONING,
            ModelTask.MATHEMATICS,
            ModelTask.EXPERT_ANALYSIS,
            ModelTask.RAG,
            ModelTask.MEMORY,
            ModelTask.WORKFLOW_PLANNING,
            ModelTask.LONG_CONTEXT,
        } and qwen3:
            score += 40
        if task is ModelTask.CODE_GENERATION:
            # The installed Qwen3 no-thinking profile is independently verified
            # at 11/12 executable cases versus 9/12 for the coder model.
            score += 100 if qwen3 else 0
            score += 30 if ModelCapability.CODE in model.capabilities else 0
        elif task is ModelTask.CODING:
            # The complete coding category favors a dedicated coder family.
            # Discovery retains that specialization as a code capability even
            # when runtime metadata normalizes its public family name.
            score += 110 if coder else 0
            score += 30 if ModelCapability.CODE in model.capabilities else 0
            score += 10 if qwen3 else 0
        elif task is ModelTask.DEBUGGING:
            # The current complete debugging contract scores higher on Qwen3;
            # the coder profile misses base-case and idempotency terminology.
            score += 100 if qwen3 else 0
            score += 25 if coder else 0
            score += 20 if ModelCapability.CODE in model.capabilities else 0
        if task is ModelTask.EXACT_OUTPUT:
            # A complete deterministic 34-case exact-output comparison scored
            # Qwen3 97.65 versus 80.97 for the installed 7B coder.  Keep this
            # evidence-backed specialization separate from general scale
            # ranking so a future model still has to earn its task route.
            score += 100 if qwen3 else 0
            score += 50 if ModelCapability.STRUCTURED_OUTPUT in model.capabilities else 0
            score += 65 if coder else 0
            score += 20 if ModelCapability.CODE in model.capabilities else 0
        if task is ModelTask.SUMMARIZATION and qwen3:
            score += 30
        if task is ModelTask.LONG_CONTEXT and model.context_window is not None:
            score += min(100, model.context_window // 2_048)
        return score


_TASK_SYSTEM_INSTRUCTIONS = {
    ModelTask.CODE_GENERATION: (
        "For code generation, silently simulate every stated edge case before "
        "answering. Preserve the requested module and export shape. Return one "
        "complete artifact only, with no examples, test calls, or afterword."
    ),
    ModelTask.DEBUGGING: (
        "Use the standard canonical name for the defect. State the direct root "
        "cause and concrete correction requested, while obeying every concise or "
        "name-only output constraint."
    ),
    ModelTask.EXACT_OUTPUT: (
        "The user's exact-output requirement is literal. Return only the requested "
        "token, JSON value, delimiter form, or line, with no added punctuation, "
        "labels, code fences, explanation, or surrounding whitespace."
    ),
    ModelTask.EXPERT_ANALYSIS: (
        "Treat every explicitly named checklist item as mandatory. Before answering, "
        "silently verify that each is covered within the requested bullet and word "
        "limits. Do not invent implementation facts."
    ),
    ModelTask.WORKFLOW_PLANNING: (
        "Treat every explicitly named rollout or recovery checkpoint as mandatory. "
        "Preserve the requested order, item count, and word limits."
    ),
}


def task_system_instruction(task: ModelTask | None) -> str | None:
    """Return a bounded trusted response contract for an automatic task route."""

    if task is not None and not isinstance(task, ModelTask):
        raise TypeError("task must be a ModelTask or None")
    return _TASK_SYSTEM_INSTRUCTIONS.get(task)
