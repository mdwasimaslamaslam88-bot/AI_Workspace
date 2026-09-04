from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent_os.contracts import AgentRunRequest, AgentRunResult, AgentRunStatus
from app.agent_os.persistence import DatabaseAgentRunStore
from app.agent_os.runtime import AgentRunManager, AgentRunNotFoundError
from app.ai.routing import ModelTask
from app.models.agent_mission import AgentMission, AgentMissionEvent
from app.models.user import User


pytestmark = pytest.mark.integration


class _Orchestrator:
    def __init__(self, status: AgentRunStatus = AgentRunStatus.COMPLETED):
        self.result = AgentRunResult(
            status=status,
            plan=None,
            output="verified" if status is AgentRunStatus.COMPLETED else None,
            attempts=(),
            failure_code=None if status is AgentRunStatus.COMPLETED else "test_failure",
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


async def _wait_for_status(
    manager: AgentRunManager,
    owner_id,
    run_id,
    expected: AgentRunStatus,
) -> None:
    for _ in range(100):
        record = await manager.get_for_owner(owner_id, run_id)
        if record is not None and record.status is expected:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"mission did not reach {expected.value}")


@pytest.mark.asyncio
async def test_persistent_mission_controls_recover_and_preserve_owner_isolation(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        foreign = User()
        session.add_all((owner, foreign))
        await session.commit()
        owner_id, foreign_id = owner.id, foreign.id

    orchestrator = _Orchestrator()
    orchestrator.block = True
    manager = AgentRunManager(
        orchestrator,
        store=DatabaseAgentRunStore(factory),
    )
    request = AgentRunRequest(
        goal="Inspect the durable scheduler.",
        task=ModelTask.GENERAL_CHAT,
        require_owner_approval=True,
    )
    submitted = await manager.submit(owner_id, request)
    assert submitted.status is AgentRunStatus.NEEDS_APPROVAL
    assert orchestrator.started.is_set() is False

    with pytest.raises(AgentRunNotFoundError):
        await manager.approve_for_owner(foreign_id, submitted.id)
    modified = await manager.modify_for_owner(
        owner_id,
        submitted.id,
        "Inspect and verify the durable scheduler.",
    )
    assert modified.status is AgentRunStatus.NEEDS_APPROVAL
    assert modified.revision == 2
    assert modified.approved is False

    approved = await manager.approve_for_owner(owner_id, submitted.id)
    assert approved.status is AgentRunStatus.QUEUED
    await orchestrator.started.wait()
    paused = await manager.pause_for_owner(owner_id, submitted.id)
    assert paused.status is AgentRunStatus.PAUSED
    assert paused.result is None
    await manager.shutdown()

    replacement = AgentRunManager(
        _Orchestrator(),
        store=DatabaseAgentRunStore(factory),
    )
    await replacement.initialize()
    recovered = await replacement.get_for_owner(owner_id, submitted.id)
    assert recovered is not None
    assert recovered.status is AgentRunStatus.PAUSED
    assert recovered.goal == "Inspect and verify the durable scheduler."
    assert await replacement.get_for_owner(foreign_id, submitted.id) is None

    resumed = await replacement.resume_for_owner(owner_id, submitted.id)
    assert resumed.status is AgentRunStatus.QUEUED
    await _wait_for_status(
        replacement,
        owner_id,
        submitted.id,
        AgentRunStatus.COMPLETED,
    )
    await replacement.shutdown()

    async with factory() as session:
        row = await session.scalar(
            select(AgentMission).where(AgentMission.id == submitted.id)
        )
        assert row is not None
        assert row.status == AgentRunStatus.COMPLETED.value
        events = list(
            (
                await session.scalars(
                    select(AgentMissionEvent)
                    .where(AgentMissionEvent.mission_id == submitted.id)
                    .order_by(AgentMissionEvent.sequence)
                )
            ).all()
        )
        actions = [event.action for event in events]
        assert {
            "approval_required",
            "modified",
            "approved",
            "pause_requested",
            "paused",
            "resumed",
        }.issubset(actions)
        modified_event = next(event for event in events if event.action == "modified")
        assert modified_event.detail_sha256 is not None
        assert "scheduler" not in modified_event.detail_sha256


@pytest.mark.asyncio
async def test_persistent_manual_retry_is_explicit_and_bounded(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(test_database_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User()
        session.add(owner)
        await session.commit()
        owner_id = owner.id

    orchestrator = _Orchestrator(AgentRunStatus.FAILED)
    manager = AgentRunManager(orchestrator, store=DatabaseAgentRunStore(factory))
    submitted = await manager.submit(
        owner_id,
        AgentRunRequest(goal="Retry safely.", task=ModelTask.GENERAL_CHAT),
    )
    await _wait_for_status(manager, owner_id, submitted.id, AgentRunStatus.FAILED)
    orchestrator.result = AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        plan=None,
        output="verified",
        attempts=(),
    )
    retried = await manager.retry_for_owner(owner_id, submitted.id)
    assert retried.manual_retry_count == 1
    await _wait_for_status(
        manager,
        owner_id,
        submitted.id,
        AgentRunStatus.COMPLETED,
    )
    await manager.shutdown()
