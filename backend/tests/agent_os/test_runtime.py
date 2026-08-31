import asyncio
from uuid import uuid4

import pytest

from app.agent_os.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent_os.runtime import AgentRunManager, AgentRunNotFoundError
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

    async def run(self, _request):
        self.started.set()
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
