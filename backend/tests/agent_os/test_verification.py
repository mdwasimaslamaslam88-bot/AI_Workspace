import asyncio
import hashlib

import pytest

from app.agent_os.contracts import (
    AgentArtifact,
    AgentExecution,
    AgentKind,
    AgentPermission,
    AgentPlanStep,
    VerificationCheck,
    VerificationFailure,
)
from app.agent_os.verification import IndependentVerificationEngine
from app.ai.routing import ModelTask


def _step(*, objective=False):
    return AgentPlanStep(
        step_id="verify-output",
        agent=AgentKind.CODING,
        task=ModelTask.CODE_GENERATION,
        instruction="Generate the artifact.",
        permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
        requires_objective_evidence=objective,
    )


@pytest.mark.asyncio
async def test_independent_verifier_hashes_untouched_artifact(tmp_path):
    generated = tmp_path / "generated.py"
    original = b"def answer():\n    return 42\n"
    generated.write_bytes(original)
    artifact = AgentArtifact(
        artifact_id="code",
        relative_path="generated.py",
        content_sha256=hashlib.sha256(original).hexdigest(),
        byte_size=len(original),
        media_type="text/x-python",
    )

    report = await IndependentVerificationEngine(
        workspace_root=tmp_path
    ).verify(
        _step(),
        AgentExecution(output="Artifact generated.", artifacts=(artifact,)),
    )

    assert report.passed is True
    assert report.checks[-1].evidence_sha256 == artifact.content_sha256
    assert generated.read_bytes() == original


@pytest.mark.asyncio
async def test_independent_verifier_detects_mutation_without_repair(tmp_path):
    generated = tmp_path / "generated.py"
    original = b"return_value = 42\n"
    generated.write_bytes(original)
    artifact = AgentArtifact(
        artifact_id="code",
        relative_path="generated.py",
        content_sha256=hashlib.sha256(original).hexdigest(),
        byte_size=len(original),
    )
    mutated = b"return_value = 7\n"
    generated.write_bytes(mutated)

    report = await IndependentVerificationEngine(
        workspace_root=tmp_path
    ).verify(
        _step(),
        AgentExecution(output="Artifact generated.", artifacts=(artifact,)),
    )

    assert report.passed is False
    assert report.checks[-1].failure is VerificationFailure.ARTIFACT_MUTATED
    assert generated.read_bytes() == mutated


@pytest.mark.asyncio
async def test_objective_evidence_is_required_and_verifier_errors_fail_closed():
    async def broken(_step, _execution):
        raise RuntimeError("must not escape")

    report = await IndependentVerificationEngine(
        objective_verifiers=(broken,)
    ).verify(_step(objective=True), AgentExecution(output="claim"))

    assert report.passed is False
    assert report.checks[-1] == VerificationCheck(
        check_id="objective-verifier-1",
        passed=False,
        failure=VerificationFailure.VERIFIER_ERROR,
    )


@pytest.mark.asyncio
async def test_non_applicable_objective_verifier_does_not_satisfy_evidence():
    async def not_applicable(_step, _execution):
        return None

    report = await IndependentVerificationEngine(
        objective_verifiers=(not_applicable,)
    ).verify(_step(objective=True), AgentExecution(output="claim"))

    assert report.passed is False
    assert report.checks[-1].failure is VerificationFailure.EVIDENCE_MISSING


@pytest.mark.asyncio
async def test_objective_verifier_does_not_swallow_task_cancellation():
    async def waiting(_step, _execution):
        await asyncio.Future()

    task = asyncio.create_task(
        IndependentVerificationEngine(
            objective_verifiers=(waiting,)
        ).verify(_step(), AgentExecution(output="claim"))
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
