from __future__ import annotations

from dataclasses import dataclass
import hashlib

from app.agent_os.contracts import (
    AgentKind,
    AgentPermission,
    AgentRunRequest,
    AgentRunStatus,
)
from app.agent_os.orchestrator import AgentOrchestrator
from app.ai.routing import ModelTask


class LearningTeacherError(RuntimeError):
    """A locally generated lesson did not pass the independent verifier."""


@dataclass(frozen=True, slots=True)
class VerifiedLessonGeneration:
    output: str
    output_sha256: str
    model_id: str


class LearningTeacherAgent:
    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        if not isinstance(orchestrator, AgentOrchestrator):
            raise TypeError("learning teacher requires the Agent OS orchestrator")
        self.orchestrator = orchestrator

    @property
    def private_context_allowed(self) -> bool:
        """Private memory requires a selector with a fail-closed local-only path."""
        selector = self.orchestrator.model_selector
        return callable(getattr(selector, "select_local", None)) or getattr(
            selector, "external", None
        ) is None

    async def generate_lesson(self, instruction: str) -> VerifiedLessonGeneration:
        if not isinstance(instruction, str) or not 1 <= len(instruction) <= 16_000:
            raise LearningTeacherError("learning lesson request is invalid")
        result = await self.orchestrator.run(
            AgentRunRequest(
                goal=instruction,
                task=ModelTask.REASONING,
                specialist=AgentKind.PLANNER,
                permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
                max_retries=1,
                deadline_seconds=120.0,
                require_objective_evidence=False,
                allow_external_models=False,
            )
        )
        if result.status is not AgentRunStatus.COMPLETED or result.output is None:
            raise LearningTeacherError("learning lesson verification failed")
        if not result.output.strip() or len(result.output) > 65_536:
            raise LearningTeacherError("learning lesson output is invalid")
        attempt = next(
            (
                value
                for value in reversed(result.attempts)
                if value.verification.passed and value.model_id is not None
            ),
            None,
        )
        digest = hashlib.sha256(result.output.encode("utf-8")).hexdigest()
        if attempt is None or attempt.verification.output_sha256 != digest:
            raise LearningTeacherError("learning lesson digest is unverified")
        return VerifiedLessonGeneration(result.output, digest, attempt.model_id)
