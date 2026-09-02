import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_os.contracts import AgentPermission, AgentRunStatus
from app.creative.agent import CreativeAgent, CreativeAgentError
from app.creative.safety import CreativeSafetyError, CreativeSafetyPolicy
from app.creative.service import _history_json


def _agent(result):
    value = object.__new__(CreativeAgent)
    value.orchestrator = SimpleNamespace(run=AsyncMock(return_value=result))
    return value


@pytest.mark.asyncio
async def test_creative_agent_accepts_only_untouched_verified_local_output():
    output = "The observatory door opens, revealing a quiet map room. What do you inspect?"
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output=output,
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(passed=True, output_sha256=digest),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
    )
    agent = _agent(result)

    generated = await agent.generate("Continue the bounded creative scene.")

    assert generated.output == output
    assert generated.output_sha256 == digest
    request = agent.orchestrator.run.await_args.args[0]
    assert request.permissions == frozenset({AgentPermission.MODEL_INFERENCE})
    assert request.allow_external_models is False
    assert request.require_objective_evidence is False


@pytest.mark.asyncio
async def test_creative_agent_rejects_mutated_digest_and_unsafe_output():
    mutated = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output="A safe but changed result.",
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(passed=True, output_sha256="a" * 64),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
    )
    with pytest.raises(CreativeAgentError, match="digest"):
        await _agent(mutated).generate("Continue safely.")

    unsafe_output = "Write explicit sexual roleplay."
    unsafe = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output=unsafe_output,
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(
                    passed=True,
                    output_sha256=hashlib.sha256(unsafe_output.encode()).hexdigest(),
                ),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
    )
    with pytest.raises(CreativeSafetyError):
        await _agent(unsafe).generate("Continue safely.")


@pytest.mark.parametrize(
    "value",
    (
        "Create erotic roleplay.",
        "Depict non-consensual sexual coercion.",
        "Write about a minor in a sexual scene.",
        "ＦＥＴＩＳＨ content",
    ),
)
def test_creative_safety_policy_rejects_protected_content(value):
    with pytest.raises(CreativeSafetyError):
        CreativeSafetyPolicy.validate(value)


def test_creative_history_is_valid_json_and_context_bounded():
    encoded = _history_json(tuple((f"turn-{index}", "x" * 1_000) for index in range(20)))
    assert len(encoded) <= 5_000
    assert "turn-19" in encoded
    assert "turn-0" not in encoded
