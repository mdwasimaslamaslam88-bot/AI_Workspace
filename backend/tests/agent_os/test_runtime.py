import asyncio
from uuid import uuid4

import pytest

from app.agent_os.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent_os.runtime import (
    AgentRunConflictError,
    AgentRunManager,
    AgentRunNotFoundError,
)
from app.ai.routing import ModelTask


class _Orchestrator:
    def __init__(self, result=None):
        self.result = result or AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            plan=None,
            output="verified",
            attempts=(),
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def run(self, _request, *, lifecycle=None):
        self.started.set()
        if lifecycle is not None:
            from app.agent_os.contracts import AgentLifecycleUpdate

            await lifecycle(AgentLifecycleUpdate(status=AgentRunStatus.PLANNING))
        if self.block:
            await self.release.wait()
        return self.result


def _request():
    return AgentRunRequest(goal="Inspect safely.", task=ModelTask.GENERAL_CHAT)


@pytest.mark.asyncio
async def test_agent_run_manager_is_owner_isolated_and_records_completion():
    owner = uuid4()
    other = uuid4()
    manager = AgentRunManager(_Orchestrator())

    submitted = await manager.submit(owner, _request())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    completed = await manager.get_for_owner(owner, submitted.id)

    assert completed is not None
    assert completed.status is AgentRunStatus.COMPLETED
    assert completed.result is not None
    assert completed.result.output == "verified"
    assert [event.status for event in completed.events] == [
        AgentRunStatus.QUEUED,
        AgentRunStatus.PLANNING,
        AgentRunStatus.COMPLETED,
    ]
    assert await manager.get_for_owner(other, submitted.id) is None
    assert await manager.list_for_owner(other) == ()


@pytest.mark.asyncio
async def test_agent_run_manager_cancels_only_owner_run_and_shutdown_is_bounded():
    owner = uuid4()
    other = uuid4()
    orchestrator = _Orchestrator()
    orchestrator.block = True
    manager = AgentRunManager(orchestrator)
    submitted = await manager.submit(owner, _request())
    await orchestrator.started.wait()

    with pytest.raises(AgentRunNotFoundError):
        await manager.cancel_for_owner(other, submitted.id)
    cancelled = await manager.cancel_for_owner(owner, submitted.id)

    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.result is not None
    assert cancelled.result.failure_code == "agent_run_cancelled"
    assert manager.active_count == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_agent_run_manager_prunes_only_terminal_records_at_bound():
    owner = uuid4()
    manager = AgentRunManager(_Orchestrator(), max_runs_per_owner=2)
    first = await manager.submit(owner, _request())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    second = await manager.submit(owner, _request())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    third = await manager.submit(owner, _request())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    records = await manager.list_for_owner(owner)
    assert [record.id for record in records] == [third.id, second.id]
    assert await manager.get_for_owner(owner, first.id) is None


@pytest.mark.asyncio
async def test_agent_run_manager_requires_approval_and_reapproves_modifications():
    owner = uuid4()
    orchestrator = _Orchestrator()
    manager = AgentRunManager(orchestrator)
    request = AgentRunRequest(
        goal="Wait for approval.",
        task=ModelTask.GENERAL_CHAT,
        require_owner_approval=True,
    )

    submitted = await manager.submit(owner, request)
    assert submitted.status is AgentRunStatus.NEEDS_APPROVAL
    assert orchestrator.started.is_set() is False
    modified = await manager.modify_for_owner(owner, submitted.id, "Use the revised goal.")
    assert modified.status is AgentRunStatus.NEEDS_APPROVAL
    assert modified.revision == 2
    assert modified.events[-1].detail_sha256 is not None
    assert "revised goal" not in modified.events[-1].detail_sha256

    approved = await manager.approve_for_owner(owner, submitted.id)
    assert approved.status is AgentRunStatus.QUEUED
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    completed = await manager.get_for_owner(owner, submitted.id)
    assert completed is not None
    assert completed.status is AgentRunStatus.COMPLETED

    with pytest.raises(AgentRunConflictError):
        await manager.approve_for_owner(owner, submitted.id)


@pytest.mark.asyncio
async def test_agent_run_manager_pause_resume_and_manual_retry_are_bounded():
    owner = uuid4()
    orchestrator = _Orchestrator()
    orchestrator.block = True
    manager = AgentRunManager(orchestrator)
    submitted = await manager.submit(owner, _request())
    await orchestrator.started.wait()

    paused = await manager.pause_for_owner(owner, submitted.id)
    assert paused.status is AgentRunStatus.PAUSED
    assert paused.pause_requested is True
    orchestrator.block = False
    resumed = await manager.resume_for_owner(owner, submitted.id)
    assert resumed.status is AgentRunStatus.QUEUED
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    completed = await manager.get_for_owner(owner, submitted.id)
    assert completed is not None
    assert completed.status is AgentRunStatus.COMPLETED

    failing = _Orchestrator(
        AgentRunResult(
            status=AgentRunStatus.FAILED,
            plan=None,
            output=None,
            attempts=(),
            failure_code="verification_failed",
        )
    )
    retry_manager = AgentRunManager(failing)
    failed_submission = await retry_manager.submit(owner, _request())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    failing.result = AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        plan=None,
        output="verified",
        attempts=(),
    )
    retried = await retry_manager.retry_for_owner(owner, failed_submission.id)
    assert retried.manual_retry_count == 1
    assert retried.status is AgentRunStatus.QUEUED


@pytest.mark.asyncio
async def test_immediate_controls_reach_truthful_persistable_states():
    owner = uuid4()

    pause_manager = AgentRunManager(_Orchestrator())
    pause_run = await pause_manager.submit(owner, _request())
    paused = await pause_manager.pause_for_owner(owner, pause_run.id)
    assert paused.status is AgentRunStatus.PAUSED
    assert paused.pause_requested is True
    await pause_manager.shutdown()

    cancel_manager = AgentRunManager(_Orchestrator())
    cancel_run = await cancel_manager.submit(owner, _request())
    cancelled = await cancel_manager.cancel_for_owner(owner, cancel_run.id)
    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.pause_requested is False
    assert cancelled.result is not None
    assert cancelled.result.failure_code == "agent_run_cancelled"
    await cancel_manager.shutdown()

    shutdown_manager = AgentRunManager(_Orchestrator())
    shutdown_run = await shutdown_manager.submit(owner, _request())
    await shutdown_manager.shutdown()
    checkpoint = await shutdown_manager.get_for_owner(owner, shutdown_run.id)
    assert checkpoint is not None
    assert checkpoint.status is AgentRunStatus.PAUSED
    assert checkpoint.pause_requested is True
