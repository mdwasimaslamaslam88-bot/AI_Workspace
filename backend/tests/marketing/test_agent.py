from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_os.contracts import AgentRunStatus
from app.marketing.agent import MarketingAgentError, OrchestratedMarketingAgent
from app.marketing.service import output_digest
from app.models.marketing import MarketingStageKind


def _agent(result):
    value = object.__new__(OrchestratedMarketingAgent)
    value.orchestrator = SimpleNamespace(run=AsyncMock(return_value=result))
    return value


@pytest.mark.asyncio
async def test_marketing_agent_returns_only_independently_verified_output():
    output = "A grounded research brief."
    attempt = SimpleNamespace(
        verification=SimpleNamespace(passed=True, output_sha256=output_digest(output)),
        model_id="ollama-local/qwen3:8b",
    )
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output=output,
        attempts=(attempt,),
        failure_code=None,
    )

    generated = await _agent(result).generate(
        MarketingStageKind.RESEARCH, "Grounded instruction"
    )

    assert generated.output == output
    assert generated.output_sha256 == output_digest(output)
    assert generated.model_id == "ollama-local/qwen3:8b"


@pytest.mark.asyncio
async def test_marketing_agent_rejects_mismatched_verification_digest():
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output="changed output",
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(passed=True, output_sha256="a" * 64),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
        failure_code=None,
    )

    with pytest.raises(MarketingAgentError, match="verification_failed"):
        await _agent(result).generate(
            MarketingStageKind.CONTENT, "Grounded instruction"
        )
