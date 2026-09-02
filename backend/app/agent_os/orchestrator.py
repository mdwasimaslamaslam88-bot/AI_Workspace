from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.agent_os.contracts import (
    AgentAttempt,
    AgentExecution,
    AgentLifecycleReporter,
    AgentLifecycleUpdate,
    AgentKind,
    AgentPlan,
    AgentPlanStep,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    VerificationCheck,
    VerificationFailure,
    VerificationReport,
)
from app.agent_os.policy import AgentPermissionDeniedError, AgentPolicy
from app.agent_os.verification import IndependentVerificationEngine
from app.ai.catalog import ModelCatalog, ModelRuntimeUnavailableError
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationRole,
    TextGenerationRouter,
)
from app.ai.routing import (
    InferenceMode,
    ModelRoutingUnavailableError,
    ModelTask,
    TaskAwareModelRouter,
)
from app.external_ai.service import ExternalAIService


class ModelSource(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ModelSelection:
    model_id: str
    inference_mode: InferenceMode
    source: ModelSource = ModelSource.LOCAL
    provider_id: str | None = None
    provider_model_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    goal: str
    step: AgentPlanStep
    model: ModelSelection
    attempt: int


@runtime_checkable
class AgentPlanner(Protocol):
    async def plan(self, request: AgentRunRequest) -> AgentPlan: ...


@runtime_checkable
class AgentModelSelector(Protocol):
    async def select(
        self,
        task: ModelTask,
        *,
        required_context_tokens: int,
        excluded_model_ids: frozenset[str],
    ) -> ModelSelection: ...


@runtime_checkable
class SpecialistAgent(Protocol):
    kind: AgentKind

    async def execute(self, context: AgentExecutionContext) -> AgentExecution: ...


_DEFAULT_SPECIALISTS = {
    ModelTask.GENERAL_CHAT: AgentKind.PLANNER,
    ModelTask.REASONING: AgentKind.PLANNER,
    ModelTask.MATHEMATICS: AgentKind.DATA,
    ModelTask.CODING: AgentKind.CODING,
    ModelTask.CODE_GENERATION: AgentKind.CODING,
    ModelTask.DEBUGGING: AgentKind.DEBUGGING,
    ModelTask.EXPERT_ANALYSIS: AgentKind.RESEARCH,
    ModelTask.VISION: AgentKind.VISION,
    ModelTask.RAG: AgentKind.RAG,
    ModelTask.MEMORY: AgentKind.RAG,
    ModelTask.SUMMARIZATION: AgentKind.PLANNER,
    ModelTask.TOOL_CALLING: AgentKind.AUTOMATION,
    ModelTask.WORKFLOW_PLANNING: AgentKind.AUTOMATION,
    ModelTask.LONG_CONTEXT: AgentKind.RESEARCH,
    ModelTask.EXACT_OUTPUT: AgentKind.PLANNER,
    ModelTask.EMBEDDING: AgentKind.RAG,
    ModelTask.IMAGE_GENERATION: AgentKind.IMAGE,
    ModelTask.IMAGE_EDITING: AgentKind.IMAGE,
    ModelTask.VOICE_INPUT: AgentKind.VOICE,
    ModelTask.VOICE_OUTPUT: AgentKind.VOICE,
}


class RuleBasedAgentPlanner:
    """Deterministic first planner; model output cannot grant permissions."""

    async def plan(self, request: AgentRunRequest) -> AgentPlan:
        if not isinstance(request, AgentRunRequest):
            raise TypeError("planner requires an AgentRunRequest")
        specialist = request.specialist or _DEFAULT_SPECIALISTS[request.task]
        return AgentPlan(
            steps=(
                AgentPlanStep(
                    step_id="execute-goal",
                    agent=specialist,
                    task=request.task,
                    instruction=request.goal,
                    permissions=request.permissions,
                    requires_objective_evidence=(
                        request.require_objective_evidence
                    ),
                ),
            )
        )


class LocalModelSelector:
    def __init__(
        self,
        catalog: ModelCatalog,
        router: TaskAwareModelRouter,
    ) -> None:
        self.catalog = catalog
        self.router = router

    async def select(
        self,
        task: ModelTask,
        *,
        required_context_tokens: int,
        excluded_model_ids: frozenset[str],
    ) -> ModelSelection:
        models = tuple(
            model
            for model in await self.catalog.list_models()
            if model.model_id not in excluded_model_ids
        )
        decision = self.router.select(
            models,
            task,
            required_context_tokens=required_context_tokens,
        )
        return ModelSelection(decision.model_id, decision.inference_mode)


class LocalFirstModelSelector:
    """Exhaust safe local routes before consulting opted-in providers."""

    def __init__(
        self,
        local: LocalModelSelector,
        external: ExternalAIService | None,
    ) -> None:
        self.local = local
        self.external = external

    async def select(
        self,
        task: ModelTask,
        *,
        required_context_tokens: int,
        excluded_model_ids: frozenset[str],
    ) -> ModelSelection:
        try:
            return await self.local.select(
                task,
                required_context_tokens=required_context_tokens,
                excluded_model_ids=excluded_model_ids,
            )
        except (ModelRoutingUnavailableError, ModelRuntimeUnavailableError):
            pass
        if self.external is None:
            raise ModelRoutingUnavailableError(
                "no local or configured external model satisfies the task"
            )
        for choice in self.external.choices(
            task,
            required_context_tokens=required_context_tokens,
        ):
            provider_id = choice.provider.config.provider_id
            provider_model_id = choice.model.model_id
            digest = hashlib.sha256(
                f"{provider_id}\x00{provider_model_id}".encode("utf-8")
            ).hexdigest()[:24]
            public_id = f"external_ai:{digest}"
            if public_id not in excluded_model_ids:
                return ModelSelection(
                    public_id,
                    (
                        InferenceMode.THINKING_DISABLED
                        if task in {ModelTask.CODE_GENERATION, ModelTask.EXACT_OUTPUT}
                        else InferenceMode.AUTO
                    ),
                    source=ModelSource.EXTERNAL,
                    provider_id=provider_id,
                    provider_model_id=provider_model_id,
                )
        raise ModelRoutingUnavailableError(
            "no local or configured external model satisfies the task"
        )


class ModelBackedSpecialist:
    """A scoped specialist that can infer but cannot invoke tools itself."""

    def __init__(
        self,
        kind: AgentKind,
        catalog: ModelCatalog,
        generation_router: TextGenerationRouter,
        *,
        external_ai: ExternalAIService | None = None,
        max_output_tokens: int = 4096,
    ) -> None:
        if not isinstance(kind, AgentKind):
            raise TypeError("specialist kind must be an AgentKind")
        if not 1 <= max_output_tokens <= 8192:
            raise ValueError("specialist output token bound is invalid")
        self.kind = kind
        self.catalog = catalog
        self.generation_router = generation_router
        self.external_ai = external_ai
        self.max_output_tokens = max_output_tokens

    async def execute(self, context: AgentExecutionContext) -> AgentExecution:
        messages = (
            TextGenerationMessage(
                TextGenerationRole.SYSTEM,
                (
                    f"You are the WORK STATION {self.kind.value} specialist. "
                    "Follow the stated goal, stay within the typed permission "
                    "contract, never claim a tool action you did not execute, "
                    "and clearly distinguish verified facts from inference."
                ),
            ),
            TextGenerationMessage(
                TextGenerationRole.USER,
                context.step.instruction,
            ),
        )
        if context.model.source is ModelSource.EXTERNAL:
            if (
                self.external_ai is None
                or context.model.provider_id is None
                or context.model.provider_model_id is None
            ):
                raise ModelRuntimeUnavailableError(
                    "external AI provider is unavailable"
                )
            generated_external = await self.external_ai.generate_selected(
                context.step.task,
                context.model.provider_id,
                context.model.provider_model_id,
                messages,
                max_output_tokens=self.max_output_tokens,
            )
            return AgentExecution(
                output=generated_external.content,
                model_id=context.model.model_id,
                evidence_codes=("external-provider",),
            )
        model = await self.catalog.resolve_model(context.model.model_id)
        if model is None:
            raise ModelRuntimeUnavailableError("selected model is unavailable")
        generated = await self.generation_router.generate(
            model,
            messages,
            max_output_tokens=self.max_output_tokens,
            **(
                {"thinking": False}
                if context.model.inference_mode
                is InferenceMode.THINKING_DISABLED
                else {}
            ),
        )
        return AgentExecution(
            output=generated.content,
            model_id=context.model.model_id,
        )


class AgentOrchestrator:
    """Bounded plan -> route -> execute -> verify -> retry lifecycle."""

    def __init__(
        self,
        planner: AgentPlanner,
        model_selector: AgentModelSelector,
        specialists: tuple[SpecialistAgent, ...],
        verifier: IndependentVerificationEngine,
        *,
        policy: AgentPolicy | None = None,
        max_active: int = 2,
    ) -> None:
        if not 1 <= max_active <= 8:
            raise ValueError("agent concurrency bound is invalid")
        self.planner = planner
        self.model_selector = model_selector
        self.verifier = verifier
        self.policy = policy or AgentPolicy()
        self._admission = asyncio.Semaphore(max_active)
        self._specialists: dict[AgentKind, SpecialistAgent] = {}
        for specialist in specialists:
            if not isinstance(specialist.kind, AgentKind):
                raise TypeError("specialist kind must be an AgentKind")
            if specialist.kind in self._specialists:
                raise ValueError(f"duplicate specialist: {specialist.kind}")
            self._specialists[specialist.kind] = specialist

    @property
    def registered_specialists(self) -> tuple[AgentKind, ...]:
        return tuple(kind for kind in AgentKind if kind in self._specialists)

    async def run(
        self,
        request: AgentRunRequest,
        lifecycle: AgentLifecycleReporter | None = None,
    ) -> AgentRunResult:
        if not isinstance(request, AgentRunRequest):
            raise TypeError("orchestrator requires an AgentRunRequest")
        try:
            async with asyncio.timeout(request.deadline_seconds):
                async with self._admission:
                    await self._report(
                        lifecycle,
                        AgentLifecycleUpdate(status=AgentRunStatus.PLANNING),
                    )
                    return await self._run_bounded(request, lifecycle)
        except TimeoutError:
            return AgentRunResult(
                status=AgentRunStatus.TIMED_OUT,
                plan=None,
                output=None,
                attempts=(),
                failure_code="agent_deadline_exceeded",
            )
        except asyncio.CancelledError:
            raise

    @staticmethod
    async def _report(
        lifecycle: AgentLifecycleReporter | None,
        update: AgentLifecycleUpdate,
    ) -> None:
        if lifecycle is not None:
            await lifecycle(update)

    async def _run_bounded(
        self,
        request: AgentRunRequest,
        lifecycle: AgentLifecycleReporter | None,
    ) -> AgentRunResult:
        try:
            plan = await self.planner.plan(request)
            self.policy.authorize(plan)
            await self._report(
                lifecycle,
                AgentLifecycleUpdate(status=AgentRunStatus.PLANNING, plan=plan),
            )
        except AgentPermissionDeniedError:
            return AgentRunResult(
                status=AgentRunStatus.FAILED,
                plan=None,
                output=None,
                attempts=(),
                failure_code="permission_denied",
            )
        attempts: list[AgentAttempt] = []
        step_outputs: list[str] = []
        for step in plan.steps:
            specialist = self._specialists.get(step.agent)
            if specialist is None:
                return AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    plan=plan,
                    output=None,
                    attempts=tuple(attempts),
                    failure_code="specialist_unavailable",
                )
            excluded: set[str] = set()
            verified_execution: AgentExecution | None = None
            for attempt_number in range(1, request.max_retries + 2):
                model: ModelSelection | None = None
                try:
                    model = await self.model_selector.select(
                        step.task,
                        required_context_tokens=request.required_context_tokens,
                        excluded_model_ids=frozenset(excluded),
                    )
                    await self._report(
                        lifecycle,
                        AgentLifecycleUpdate(
                            status=AgentRunStatus.RUNNING,
                            plan=plan,
                            step_id=step.step_id,
                            attempt=attempt_number,
                            agent=step.agent,
                            model_id=model.model_id,
                        ),
                    )
                    execution = await specialist.execute(
                        AgentExecutionContext(
                            goal=request.goal,
                            step=step,
                            model=model,
                            attempt=attempt_number,
                        )
                    )
                    await self._report(
                        lifecycle,
                        AgentLifecycleUpdate(
                            status=AgentRunStatus.VERIFYING,
                            plan=plan,
                            step_id=step.step_id,
                            attempt=attempt_number,
                            agent=step.agent,
                            model_id=model.model_id,
                        ),
                    )
                    report = await self.verifier.verify(step, execution)
                except (
                    ModelRoutingUnavailableError,
                    ModelRuntimeUnavailableError,
                ):
                    break
                except Exception:
                    report = VerificationReport(
                        passed=False,
                        checks=(
                            VerificationCheck(
                                check_id="specialist-execution",
                                passed=False,
                                failure=VerificationFailure.VERIFIER_ERROR,
                            ),
                        ),
                        output_sha256=hashlib.sha256(b"").hexdigest(),
                    )
                    attempts.append(
                        AgentAttempt(
                            step_id=step.step_id,
                            attempt=attempt_number,
                            agent=step.agent,
                            model_id=(model.model_id if model is not None else None),
                            verification=report,
                        )
                    )
                    if model is not None:
                        excluded.add(model.model_id)
                    if attempt_number <= request.max_retries:
                        await self._report(
                            lifecycle,
                            AgentLifecycleUpdate(
                                status=AgentRunStatus.RETRYING,
                                plan=plan,
                                step_id=step.step_id,
                                attempt=attempt_number,
                                agent=step.agent,
                                model_id=(model.model_id if model is not None else None),
                            ),
                        )
                    continue
                attempts.append(
                    AgentAttempt(
                        step_id=step.step_id,
                        attempt=attempt_number,
                        agent=step.agent,
                        model_id=model.model_id,
                        verification=report,
                    )
                )
                excluded.add(model.model_id)
                if report.passed:
                    verified_execution = execution
                    break
                if attempt_number <= request.max_retries:
                    await self._report(
                        lifecycle,
                        AgentLifecycleUpdate(
                            status=AgentRunStatus.RETRYING,
                            plan=plan,
                            step_id=step.step_id,
                            attempt=attempt_number,
                            agent=step.agent,
                            model_id=model.model_id,
                        ),
                    )
            if verified_execution is None:
                return AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    plan=plan,
                    output=None,
                    attempts=tuple(attempts),
                    failure_code="verification_failed",
                )
            step_outputs.append(verified_execution.output)
        return AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            plan=plan,
            output="\n\n".join(step_outputs),
            attempts=tuple(attempts),
        )
