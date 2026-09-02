import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_os.contracts import (
    AgentExecution,
    AgentKind,
    AgentPermission,
    AgentRunRequest,
    AgentRunStatus,
    VerificationCheck,
    VerificationFailure,
)
from app.agent_os.orchestrator import (
    AgentOrchestrator,
    AgentExecutionContext,
    LocalFirstModelSelector,
    ModelBackedSpecialist,
    ModelSelection,
    ModelSource,
    RuleBasedAgentPlanner,
)
from app.agent_os.verification import IndependentVerificationEngine
from app.ai.routing import InferenceMode, ModelTask
from app.ai.routing import ModelRoutingUnavailableError
from app.external_ai.contracts import (
    ExternalGenerationResult,
    ExternalModelPolicy,
    ExternalProviderConfig,
    ExternalProviderKind,
    ExternalProviderRecord,
)
from app.external_ai.service import ExternalModelChoice


MODEL_ONE = f"ollama:{'1' * 24}"
MODEL_TWO = f"ollama:{'2' * 24}"


def _request(**values):
    return AgentRunRequest(
        goal="Diagnose the failure and return evidence.",
        task=ModelTask.DEBUGGING,
        specialist=AgentKind.DEBUGGING,
        permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
        **values,
    )


@pytest.mark.asyncio
async def test_orchestrator_plans_routes_executes_verifies_and_returns_result():
    selector = Mock(
        select=AsyncMock(
            return_value=ModelSelection(MODEL_ONE, InferenceMode.AUTO)
        )
    )
    specialist = Mock(
        kind=AgentKind.DEBUGGING,
        execute=AsyncMock(
            return_value=AgentExecution(
                output="Root cause with reproducible evidence.",
                model_id=MODEL_ONE,
            )
        ),
    )
    orchestrator = AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    )

    result = await orchestrator.run(_request())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.output == "Root cause with reproducible evidence."
    assert result.attempts[0].verification.passed is True
    selector.select.assert_awaited_once_with(
        ModelTask.DEBUGGING,
        required_context_tokens=0,
        excluded_model_ids=frozenset(),
    )


@pytest.mark.asyncio
async def test_orchestrator_retries_with_an_alternate_model_after_failed_verification():
    selections = [
        ModelSelection(MODEL_ONE, InferenceMode.AUTO),
        ModelSelection(MODEL_TWO, InferenceMode.AUTO),
    ]
    selector = Mock(select=AsyncMock(side_effect=selections))
    specialist = Mock(
        kind=AgentKind.DEBUGGING,
        execute=AsyncMock(
            side_effect=(
                AgentExecution(output="", model_id=MODEL_ONE),
                AgentExecution(output="Verified repair.", model_id=MODEL_TWO),
            )
        ),
    )
    orchestrator = AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    )

    lifecycle = []

    async def report(update):
        lifecycle.append(update)

    result = await orchestrator.run(_request(max_retries=1), lifecycle=report)

    assert result.status is AgentRunStatus.COMPLETED
    assert [attempt.model_id for attempt in result.attempts] == [
        MODEL_ONE,
        MODEL_TWO,
    ]
    assert result.attempts[0].verification.checks[0].failure is (
        VerificationFailure.EMPTY_OUTPUT
    )
    assert selector.select.await_args_list[1].kwargs["excluded_model_ids"] == {
        MODEL_ONE
    }
    assert [update.status for update in lifecycle] == [
        AgentRunStatus.PLANNING,
        AgentRunStatus.PLANNING,
        AgentRunStatus.RUNNING,
        AgentRunStatus.VERIFYING,
        AgentRunStatus.RETRYING,
        AgentRunStatus.RUNNING,
        AgentRunStatus.VERIFYING,
    ]
    assert lifecycle[1].plan is not None
    assert lifecycle[2].model_id == MODEL_ONE
    assert lifecycle[-1].model_id == MODEL_TWO


@pytest.mark.asyncio
async def test_orchestrator_stops_after_bounded_failures():
    selector = Mock(
        select=AsyncMock(
            side_effect=(
                ModelSelection(MODEL_ONE, InferenceMode.AUTO),
                ModelSelection(MODEL_TWO, InferenceMode.AUTO),
            )
        )
    )
    specialist = Mock(
        kind=AgentKind.DEBUGGING,
        execute=AsyncMock(side_effect=(AgentExecution(""), AgentExecution(""))),
    )
    result = await AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    ).run(_request(max_retries=1))

    assert result.status is AgentRunStatus.FAILED
    assert result.failure_code == "verification_failed"
    assert len(result.attempts) == 2


@pytest.mark.asyncio
async def test_orchestrator_bounds_unexpected_selector_failures_without_secondary_error():
    selector = Mock(select=AsyncMock(side_effect=RuntimeError("selector fault")))
    specialist = Mock(kind=AgentKind.DEBUGGING, execute=AsyncMock())

    result = await AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    ).run(_request(max_retries=1))

    assert result.status is AgentRunStatus.FAILED
    assert result.failure_code == "verification_failed"
    assert len(result.attempts) == 2
    assert all(attempt.model_id is None for attempt in result.attempts)
    specialist.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_enforces_deadline_and_propagates_cancellation():
    selector = Mock(
        select=AsyncMock(
            return_value=ModelSelection(MODEL_ONE, InferenceMode.AUTO)
        )
    )

    async def wait_forever(_context):
        await asyncio.Future()

    specialist = Mock(kind=AgentKind.DEBUGGING, execute=wait_forever)
    orchestrator = AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    )

    result = await orchestrator.run(_request(deadline_seconds=0.001))
    assert result.status is AgentRunStatus.TIMED_OUT
    assert result.failure_code == "agent_deadline_exceeded"

    task = asyncio.create_task(orchestrator.run(_request(deadline_seconds=10)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_orchestrator_denies_specialist_privilege_escalation():
    request = AgentRunRequest(
        goal="Write into the workspace.",
        task=ModelTask.EXPERT_ANALYSIS,
        specialist=AgentKind.RESEARCH,
        permissions=frozenset({AgentPermission.WORKSPACE_WRITE}),
    )
    selector = Mock(select=AsyncMock())
    specialist = Mock(kind=AgentKind.RESEARCH, execute=AsyncMock())

    result = await AgentOrchestrator(
        RuleBasedAgentPlanner(),
        selector,
        (specialist,),
        IndependentVerificationEngine(),
    ).run(request)

    assert result.status is AgentRunStatus.FAILED
    assert result.failure_code == "permission_denied"
    selector.select.assert_not_awaited()
    specialist.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_first_selector_exhausts_local_models_before_external_fallback():
    local = Mock(
        select=AsyncMock(
            return_value=ModelSelection(MODEL_ONE, InferenceMode.AUTO)
        )
    )
    external = Mock(choices=Mock())
    selector = LocalFirstModelSelector(local, external)

    selected = await selector.select(
        ModelTask.REASONING,
        required_context_tokens=4096,
        excluded_model_ids=frozenset(),
    )

    assert selected.model_id == MODEL_ONE
    assert selected.source is ModelSource.LOCAL
    external.choices.assert_not_called()


@pytest.mark.asyncio
async def test_local_first_selector_uses_only_verified_external_policy_after_local_exhaustion():
    local = Mock(
        select=AsyncMock(side_effect=ModelRoutingUnavailableError("local exhausted"))
    )
    model = ExternalModelPolicy(
        model_id="external-reasoner",
        tasks=frozenset({ModelTask.REASONING}),
        verified=True,
        verification_evidence_sha256="d" * 64,
        measured_quality=95,
        stability_rate=1,
        context_window=32_768,
    )
    provider = ExternalProviderRecord(
        ExternalProviderConfig(
            "external-primary",
            ExternalProviderKind.OPENAI,
            enabled=True,
            models=(model,),
        ),
        "external-provider-secret-key",
    )
    external = Mock(
        choices=Mock(return_value=(ExternalModelChoice(provider, model),))
    )

    selected = await LocalFirstModelSelector(local, external).select(
        ModelTask.REASONING,
        required_context_tokens=16_000,
        excluded_model_ids=frozenset(),
    )

    assert selected.source is ModelSource.EXTERNAL
    assert selected.model_id.startswith("external_ai:")
    assert selected.provider_id == "external-primary"
    assert selected.provider_model_id == "external-reasoner"
    external.choices.assert_called_once_with(
        ModelTask.REASONING,
        required_context_tokens=16_000,
    )


@pytest.mark.asyncio
async def test_model_backed_specialist_sends_keyless_messages_to_external_service():
    external = Mock(
        generate_selected=AsyncMock(
            return_value=ExternalGenerationResult(
                content="verified fallback result",
                provider_id="external-primary",
                model_id="external-reasoner",
                input_tokens=10,
                output_tokens=4,
                cost_micros=1,
            )
        )
    )
    specialist = ModelBackedSpecialist(
        AgentKind.RESEARCH,
        Mock(),
        Mock(),
        external_ai=external,
    )
    step = (await RuleBasedAgentPlanner().plan(
        AgentRunRequest(
            goal="Analyze the evidence.",
            task=ModelTask.REASONING,
            specialist=AgentKind.RESEARCH,
        )
    )).steps[0]
    selection = ModelSelection(
        f"external_ai:{'e' * 24}",
        InferenceMode.AUTO,
        source=ModelSource.EXTERNAL,
        provider_id="external-primary",
        provider_model_id="external-reasoner",
    )

    execution = await specialist.execute(
        AgentExecutionContext(
            goal="Analyze the evidence.",
            step=step,
            model=selection,
            attempt=1,
        )
    )

    assert execution.output == "verified fallback result"
    assert execution.model_id == selection.model_id
    assert execution.evidence_codes == ("external-provider",)
    messages = external.generate_selected.await_args.args[3]
    assert all("secret" not in message.content for message in messages)
