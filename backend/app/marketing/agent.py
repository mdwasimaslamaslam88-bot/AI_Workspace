from __future__ import annotations

from dataclasses import dataclass

from app.agent_os.contracts import (
    AgentKind,
    AgentPermission,
    AgentRunRequest,
    AgentRunStatus,
)
from app.agent_os.orchestrator import AgentOrchestrator
from app.ai.routing import ModelTask
from app.marketing.service import output_digest
from app.models.marketing import (
    MAX_MARKETING_STAGE_OUTPUT_CHARACTERS,
    MarketingStageKind,
)


class MarketingAgentError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MarketingGeneration:
    output: str
    output_sha256: str
    model_id: str


_STAGE_ROUTES = {
    MarketingStageKind.RESEARCH: (ModelTask.EXPERT_ANALYSIS, AgentKind.RESEARCH),
    MarketingStageKind.STRATEGY: (ModelTask.REASONING, AgentKind.PLANNER),
    MarketingStageKind.CONTENT: (ModelTask.GENERAL_CHAT, AgentKind.PLANNER),
    MarketingStageKind.CREATIVE: (ModelTask.GENERAL_CHAT, AgentKind.PLANNER),
}


class OrchestratedMarketingAgent:
    """Compose existing local-first specialists without granting tool authority."""

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        if not isinstance(orchestrator, AgentOrchestrator):
            raise TypeError("marketing agent requires the Agent OS orchestrator")
        self.orchestrator = orchestrator

    async def generate(
        self,
        stage: MarketingStageKind,
        instruction: str,
    ) -> MarketingGeneration:
        route = _STAGE_ROUTES.get(stage)
        if route is None:
            raise MarketingAgentError("agent_failed")
        task, specialist = route
        result = await self.orchestrator.run(
            AgentRunRequest(
                goal=instruction,
                task=task,
                specialist=specialist,
                permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
                max_retries=1,
                deadline_seconds=120.0,
            )
        )
        if result.status is not AgentRunStatus.COMPLETED or result.output is None:
            raise MarketingAgentError(
                "verification_failed"
                if result.failure_code == "verification_failed"
                else "agent_failed"
            )
        if (
            not result.output.strip()
            or len(result.output) > MAX_MARKETING_STAGE_OUTPUT_CHARACTERS
        ):
            raise MarketingAgentError("verification_failed")
        verified_attempt = next(
            (
                attempt
                for attempt in reversed(result.attempts)
                if attempt.verification.passed and attempt.model_id is not None
            ),
            None,
        )
        digest = output_digest(result.output)
        if verified_attempt is None or verified_attempt.verification.output_sha256 != digest:
            raise MarketingAgentError("verification_failed")
        return MarketingGeneration(
            output=result.output,
            output_sha256=digest,
            model_id=verified_attempt.model_id,
        )
