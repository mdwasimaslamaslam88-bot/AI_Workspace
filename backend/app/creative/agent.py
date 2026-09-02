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
from app.creative.safety import CreativeSafetyPolicy


class CreativeAgentError(RuntimeError):
    """A local creative response did not pass independent verification."""


@dataclass(frozen=True, slots=True)
class VerifiedCreativeGeneration:
    output: str
    output_sha256: str
    model_id: str


class CreativeAgent:
    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        if not isinstance(orchestrator, AgentOrchestrator):
            raise TypeError("creative agent requires the Agent OS orchestrator")
        self.orchestrator = orchestrator

    async def generate(self, instruction: str) -> VerifiedCreativeGeneration:
        if not isinstance(instruction, str) or not 1 <= len(instruction) <= 16_000:
            raise CreativeAgentError("creative request is invalid")
        result = await self.orchestrator.run(
            AgentRunRequest(
                goal=instruction,
                task=ModelTask.GENERAL_CHAT,
                specialist=AgentKind.PLANNER,
                permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
                max_retries=1,
                deadline_seconds=120.0,
                require_objective_evidence=False,
                allow_external_models=False,
            )
        )
        if result.status is not AgentRunStatus.COMPLETED or result.output is None:
            raise CreativeAgentError("creative generation verification failed")
        if not result.output.strip() or len(result.output) > 32_768:
            raise CreativeAgentError("creative generation output is invalid")
        CreativeSafetyPolicy.validate(result.output)
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
            raise CreativeAgentError("creative generation digest is unverified")
        return VerifiedCreativeGeneration(result.output, digest, attempt.model_id)
