import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.models.tool import ToolExecution, ToolExecutionStatus
from app.models.user import User
from app.models.workflow import WorkflowStatus, WorkflowStep
from app.services.workflow import (
    WorkflowRunner,
    WorkflowService,
    WorkflowStepDraft,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_workflow_executes_fixed_tool_with_owner_isolation_and_audit(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(
        test_database_engine,
        expire_on_commit=False,
    )
    async with factory() as session:
        owner = User()
        other = User()
        session.add_all((owner, other))
        await session.commit()
        owner_id = owner.id
        other_id = other.id
        workflow = await WorkflowService(session).create_for_owner(
            owner_id,
            "Calculate",
            (WorkflowStepDraft("calculator", {"expression": "6*7"}),),
        )
        assert await WorkflowService(session).get_for_owner(
            other_id, workflow.id
        ) is None

    tasks = {}
    runner = WorkflowRunner(factory, asyncio.Semaphore(1), tasks)
    await runner.start_for_owner(owner_id, workflow.id)
    task = tasks[workflow.id]
    await asyncio.wait_for(asyncio.shield(task), timeout=5)

    async with factory() as session:
        completed = await WorkflowService(session).get_for_owner(
            owner_id, workflow.id
        )
        assert completed is not None
        assert completed.status is WorkflowStatus.COMPLETED
        assert completed.current_step_position == 1
        assert completed.result == {
            "steps": [
                {
                    "position": 1,
                    "tool_name": "calculator",
                    "result": {"value": 42},
                }
            ]
        }
        assert completed.steps[0].status is WorkflowStatus.COMPLETED
        assert completed.steps[0].permission == "utility"
        assert completed.steps[0].result == {"value": 42}
        assert completed.steps[0].tool_execution_id is not None

        execution = (
            await session.execute(
                select(ToolExecution).where(
                    ToolExecution.id
                    == completed.steps[0].tool_execution_id
                )
            )
        ).scalar_one()
        assert execution.owner_id == owner_id
        assert execution.status is ToolExecutionStatus.COMPLETED
        assert execution.initiator == "workflow"
        step = (
            await session.execute(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_id == workflow.id
                )
            )
        ).scalar_one()
        assert step.owner_id == owner_id


@pytest.mark.asyncio
async def test_pending_workflow_cancel_is_terminal_and_executes_no_tool(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(
        test_database_engine,
        expire_on_commit=False,
    )
    async with factory() as session:
        owner = User()
        session.add(owner)
        await session.commit()
        owner_id = owner.id
        workflow = await WorkflowService(session).create_for_owner(
            owner_id,
            "Cancel",
            (WorkflowStepDraft("local_time", {"timezone": "UTC"}),),
        )

    runner = WorkflowRunner(factory, asyncio.Semaphore(1), {})
    cancelled = await runner.cancel_for_owner(owner_id, workflow.id)

    assert cancelled.status is WorkflowStatus.CANCELLED
    assert cancelled.error_code == "workflow_cancelled"
    assert cancelled.steps[0].status is WorkflowStatus.CANCELLED
    assert cancelled.steps[0].error_code == "not_run"
    assert cancelled.steps[0].tool_execution_id is None


@pytest.mark.asyncio
async def test_database_rejects_step_owner_that_differs_from_workflow_owner(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(
        test_database_engine,
        expire_on_commit=False,
    )
    async with factory() as session:
        owner = User()
        other = User()
        session.add_all((owner, other))
        await session.commit()
        workflow = await WorkflowService(session).create_for_owner(
            owner.id,
            "Owned",
            (WorkflowStepDraft("calculator", {"expression": "1+1"}),),
        )

        async with session.begin_nested():
            with pytest.raises(IntegrityError):
                session.add(
                    WorkflowStep(
                        workflow_id=workflow.id,
                        owner_id=other.id,
                        position=2,
                        tool_name="calculator",
                        permission="utility",
                        arguments_json='{"expression":"2+2"}',
                        status=WorkflowStatus.PENDING,
                    )
                )
                await session.flush()


@pytest.mark.asyncio
async def test_competing_runners_claim_a_pending_workflow_exactly_once(
    test_database_engine: AsyncEngine,
):
    factory = async_sessionmaker(
        test_database_engine,
        expire_on_commit=False,
    )
    async with factory() as session:
        owner = User()
        session.add(owner)
        await session.commit()
        workflow = await WorkflowService(session).create_for_owner(
            owner.id,
            "One claim",
            (WorkflowStepDraft("calculator", {"expression": "20+22"}),),
        )

    admission = asyncio.Semaphore(0)
    first_tasks = {}
    second_tasks = {}
    first = WorkflowRunner(factory, admission, first_tasks)
    second = WorkflowRunner(factory, admission, second_tasks)
    await first.start_for_owner(owner.id, workflow.id)
    await second.start_for_owner(owner.id, workflow.id)
    admission.release()
    admission.release()
    await asyncio.gather(
        first_tasks[workflow.id],
        second_tasks[workflow.id],
    )

    async with factory() as session:
        completed = await WorkflowService(session).get_for_owner(
            owner.id, workflow.id
        )
        execution_count = await session.scalar(
            select(func.count(ToolExecution.id)).where(
                ToolExecution.owner_id == owner.id,
                ToolExecution.initiator == "workflow",
            )
        )
    assert completed is not None
    assert completed.status is WorkflowStatus.COMPLETED
    assert execution_count == 1
