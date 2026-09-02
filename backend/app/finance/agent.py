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
from app.models.finance import FinanceArtifactKind


class MarketIntelligenceError(RuntimeError):
    """A local market research artifact did not pass independent verification."""


@dataclass(frozen=True, slots=True)
class VerifiedMarketGeneration:
    output: str
    output_sha256: str
    model_id: str


_ROUTES = {
    FinanceArtifactKind.RESEARCH: (ModelTask.EXPERT_ANALYSIS, AgentKind.RESEARCH),
    FinanceArtifactKind.STRATEGY: (ModelTask.REASONING, AgentKind.PLANNER),
}


class MarketIntelligenceAgent:
    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        if not isinstance(orchestrator, AgentOrchestrator):
            raise TypeError("market intelligence requires the Agent OS orchestrator")
        self.orchestrator = orchestrator

    async def generate(
        self, kind: FinanceArtifactKind, instruction: str
    ) -> VerifiedMarketGeneration:
        route = _ROUTES.get(kind)
        if route is None or not 1 <= len(instruction) <= 16_000:
            raise MarketIntelligenceError("market research request is invalid")
        task, specialist = route
        result = await self.orchestrator.run(
            AgentRunRequest(
                goal=instruction,
                task=task,
                specialist=specialist,
                permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
                max_retries=1,
                deadline_seconds=120.0,
                require_objective_evidence=True,
            )
        )
        if result.status is not AgentRunStatus.COMPLETED or result.output is None:
            raise MarketIntelligenceError("market research verification failed")
        if not result.output.strip() or len(result.output) > 65_536:
            raise MarketIntelligenceError("market research output is invalid")
        attempt = next(
            (
                value
                for value in reversed(result.attempts)
                if value.verification.passed and value.model_id is not None
            ),
            None,
        )
        digest = hashlib.sha256(result.output.encode("utf-8")).hexdigest()
        if (
            attempt is None
            or attempt.verification.output_sha256
            != digest
        ):
            raise MarketIntelligenceError("market research digest is unverified")
        return VerifiedMarketGeneration(
            output=result.output,
            output_sha256=digest,
            model_id=attempt.model_id,
        )
