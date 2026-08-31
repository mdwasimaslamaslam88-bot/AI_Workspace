from dataclasses import replace
import hashlib

import pytest

from app.agent_os.contracts import (
    AgentArtifact,
    AgentExecution,
    AgentKind,
    AgentPermission,
    AgentPlan,
    AgentPlanStep,
    AgentRunRequest,
)
from app.agent_os.policy import AgentPermissionDeniedError, AgentPolicy
from app.ai.routing import ModelTask


def test_agent_contracts_are_typed_bounded_and_fail_closed():
    request = AgentRunRequest(
        goal="Implement and verify the smallest coherent change.",
        task=ModelTask.CODING,
        specialist=AgentKind.CODING,
        permissions=frozenset(
            {
                AgentPermission.MODEL_INFERENCE,
                AgentPermission.WORKSPACE_READ,
            }
        ),
        max_retries=2,
    )
    step = AgentPlanStep(
        step_id="inspect-project",
        agent=request.specialist,
        task=request.task,
        instruction=request.goal,
        permissions=request.permissions,
    )

    AgentPolicy().authorize(AgentPlan((step,)))

    with pytest.raises(ValueError, match="retry"):
        replace(request, max_retries=3)
    with pytest.raises(ValueError, match="goal"):
        replace(request, goal="   ")


def test_agent_policy_rejects_privilege_escalation_before_execution():
    plan = AgentPlan(
        (
            AgentPlanStep(
                step_id="research",
                agent=AgentKind.RESEARCH,
                task=ModelTask.EXPERT_ANALYSIS,
                instruction="Inspect current primary sources.",
                permissions=frozenset(
                    {
                        AgentPermission.NETWORK_RESEARCH,
                        AgentPermission.WORKSPACE_WRITE,
                    }
                ),
            ),
        )
    )

    with pytest.raises(AgentPermissionDeniedError, match="unauthorized"):
        AgentPolicy().authorize(plan)


def test_artifact_contract_preserves_original_digest():
    original = b"print('original')\n"
    artifact = AgentArtifact(
        artifact_id="generated-code",
        relative_path="generated/artifact.py",
        content_sha256=hashlib.sha256(original).hexdigest(),
        byte_size=len(original),
        media_type="text/x-python",
    )

    execution = AgentExecution(
        output="Generated an original artifact.",
        artifacts=(artifact,),
    )

    assert execution.artifacts[0].content_sha256 == hashlib.sha256(original).hexdigest()
