import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import ToolExecutionStatus
from app.models.workflow import Workflow, WorkflowStatus, WorkflowStep
from app.services.tool import ToolExecutionRecord
from app.services.workflow import (
    WorkflowInputInvalidError,
    WorkflowRunner,
    WorkflowService,
    WorkflowStepDraft,
    reconcile_workflows,
)


def _workflow(owner_id, *, status=WorkflowStatus.PENDING, completed=False):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    workflow = Workflow(
        id=uuid4(),
        owner_id=owner_id,
        name="Research",
        status=status,
        step_count=1,
        current_step_position=(1 if status is not WorkflowStatus.PENDING else None),
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        started_at=(now if status is not WorkflowStatus.PENDING else None),
        completed_at=(now if completed else None),
        result_json=(
            '{"steps":[{"position":1,"result":{"value":42},"tool_name":"calculator"}]}'
            if completed
            else None
        ),
    )
    step_status = WorkflowStatus.COMPLETED if completed else WorkflowStatus.PENDING
    workflow.steps = [
        WorkflowStep(
            id=uuid4(),
            workflow_id=workflow.id,
            owner_id=owner_id,
            position=1,
            tool_name="calculator",
            permission="utility",
            arguments_json='{"expression":"6*7"}',
            status=step_status,
            tool_execution_id=(uuid4() if completed else None),
            result_json='{"value":42}' if completed else None,
            created_at=now,
            started_at=now if completed else None,
            completed_at=now if completed else None,
            duration_ms=1 if completed else None,
        )
    ]
    return workflow


@pytest.mark.asyncio
async def test_create_validates_every_step_and_captures_server_permission():
    owner_id = uuid4()
    created = _workflow(owner_id)
    session = AsyncMock(spec=AsyncSession)
    repository = Mock(create=AsyncMock(return_value=created))
    service = WorkflowService(session)
    service.repository = repository

    record = await service.create_for_owner(
        owner_id,
        "Research",
        (WorkflowStepDraft("calculator", {"expression": "6*7"}),),
    )

    assert record.status is WorkflowStatus.PENDING
    repository.create.assert_awaited_once_with(
        owner_id,
        "Research",
        (("calculator", "utility", '{"expression":"6*7"}'),),
    )
    session.commit.assert_awaited_once_with()


@pytest.mark.parametrize(
    "steps",
    [
        (),
        tuple(WorkflowStepDraft("calculator", {"expression": "1"}) for _ in range(9)),
        (WorkflowStepDraft("shell", {"command": "id"}),),
        (WorkflowStepDraft("calculator", {"expression": "1", "extra": True}),),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_unregistered_workflow_never_reaches_database(steps):
    session = AsyncMock(spec=AsyncSession)
    service = WorkflowService(session)
    service.repository = Mock(create=AsyncMock())

    with pytest.raises(WorkflowInputInvalidError):
        await service.create_for_owner(uuid4(), None, steps)

    service.repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_executes_one_step_rechecks_permission_and_completes(monkeypatch):
    owner_id = uuid4()
    workflow = _workflow(owner_id, status=WorkflowStatus.RUNNING)
    completed = _workflow(owner_id, status=WorkflowStatus.RUNNING, completed=True)
    completed.id = workflow.id
    completed.steps[0].workflow_id = workflow.id
    session = AsyncMock(spec=AsyncSession)
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = Mock(return_value=context)
    repository = Mock()
    repository.claim_for_owner = AsyncMock(return_value=True)
    repository.get_for_owner = AsyncMock(
        side_effect=[workflow, workflow, completed]
    )
    repository.claim_step_for_owner = AsyncMock(return_value=True)
    repository.finish_step_for_owner = AsyncMock(return_value=True)
    repository.complete_for_owner = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.workflow.WorkflowRepository",
        Mock(return_value=repository),
    )
    execution = ToolExecutionRecord(
        id=uuid4(),
        conversation_id=None,
        tool_name="calculator",
        permission="utility",
        status=ToolExecutionStatus.COMPLETED,
        initiator="explicit_user",
        arguments={"expression": "6*7"},
        result={"value": 42},
        error_code=None,
        started_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        duration_ms=1,
    )
    tool_service = Mock(execute_for_owner=AsyncMock(return_value=execution))
    monkeypatch.setattr(
        "app.services.workflow.ToolService", Mock(return_value=tool_service)
    )
    runner = WorkflowRunner(factory, asyncio.Semaphore(1), {})

    await runner._run_claimed(owner_id, workflow.id)

    tool_service.execute_for_owner.assert_awaited_once_with(
        owner_id,
        "calculator",
        {"expression": "6*7"},
        initiator="workflow",
    )
    assert repository.finish_step_for_owner.await_args.kwargs["tool_execution_id"] == execution.id
    repository.complete_for_owner.assert_awaited_once()
    assert session.commit.await_count == 4


@pytest.mark.asyncio
async def test_wall_deadline_deterministically_times_out(monkeypatch):
    runner = WorkflowRunner(
        Mock(), asyncio.Semaphore(1), {}, wall_seconds=0.01
    )
    gate = asyncio.Event()

    async def block(*_args):
        await gate.wait()

    runner._run_claimed = AsyncMock(side_effect=block)
    runner._terminalize = AsyncMock()
    owner_id = uuid4()
    workflow_id = uuid4()

    await runner._run(owner_id, workflow_id)

    runner._terminalize.assert_awaited_once_with(
        owner_id,
        workflow_id,
        WorkflowStatus.TIMED_OUT,
        "workflow_timed_out",
    )


@pytest.mark.asyncio
async def test_task_cancellation_deterministically_cancels(monkeypatch):
    runner = WorkflowRunner(Mock(), asyncio.Semaphore(1), {})
    gate = asyncio.Event()

    async def block(*_args):
        await gate.wait()

    runner._run_claimed = AsyncMock(side_effect=block)
    runner._terminalize = AsyncMock()
    owner_id = uuid4()
    workflow_id = uuid4()
    task = asyncio.create_task(runner._run(owner_id, workflow_id))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    runner._terminalize.assert_awaited_once_with(
        owner_id,
        workflow_id,
        WorkflowStatus.CANCELLED,
        "workflow_cancelled",
    )


@pytest.mark.asyncio
async def test_startup_reconciliation_closes_interrupted_workflows(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = Mock(return_value=context)
    repository = Mock(reconcile_interrupted=AsyncMock(return_value=3))
    monkeypatch.setattr(
        "app.services.workflow.WorkflowRepository",
        Mock(return_value=repository),
    )

    assert await reconcile_workflows(factory) == 3
    repository.reconcile_interrupted.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
