from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.models.workflow import Workflow, WorkflowStatus, WorkflowStep
from app.repositories.base import BaseRepository

MAX_WORKFLOW_HISTORY = 50


class WorkflowRepository(BaseRepository):
    async def create(
        self,
        owner_id: UUID,
        name: str | None,
        steps: tuple[tuple[str, str, str], ...],
    ) -> Workflow:
        workflow = Workflow(
            owner_id=owner_id,
            name=name,
            status=WorkflowStatus.PENDING,
            step_count=len(steps),
            cancel_requested=False,
        )
        workflow.steps = [
            WorkflowStep(
                owner_id=owner_id,
                position=position,
                tool_name=tool_name,
                permission=permission,
                arguments_json=arguments_json,
                status=WorkflowStatus.PENDING,
            )
            for position, (tool_name, permission, arguments_json) in enumerate(
                steps, start=1
            )
        ]
        self.session.add(workflow)
        await self.session.flush()
        return workflow

    async def get_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> Workflow | None:
        result = await self.session.execute(
            select(Workflow)
            .options(selectinload(Workflow.steps))
            .where(Workflow.id == workflow_id, Workflow.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 20
    ) -> tuple[Workflow, ...]:
        if not 1 <= limit <= MAX_WORKFLOW_HISTORY:
            raise ValueError("workflow history limit is outside its bound")
        result = await self.session.execute(
            select(Workflow)
            .options(selectinload(Workflow.steps))
            .where(Workflow.owner_id == owner_id)
            .order_by(Workflow.created_at.desc(), Workflow.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().unique().all())

    async def claim_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> bool:
        result = await self.session.execute(
            update(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status == WorkflowStatus.PENDING,
                Workflow.cancel_requested.is_(False),
            )
            .values(
                status=WorkflowStatus.RUNNING,
                current_step_position=1,
                started_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def claim_step_for_owner(
        self, owner_id: UUID, workflow_id: UUID, position: int
    ) -> bool:
        runnable_workflow = (
            select(Workflow.id)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status == WorkflowStatus.RUNNING,
                Workflow.cancel_requested.is_(False),
            )
            .exists()
        )
        result = await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.owner_id == owner_id,
                WorkflowStep.position == position,
                WorkflowStep.status == WorkflowStatus.PENDING,
                runnable_workflow,
            )
            .values(status=WorkflowStatus.RUNNING, started_at=func.now())
        )
        return result.rowcount == 1

    async def finish_step_for_owner(
        self,
        owner_id: UUID,
        workflow_id: UUID,
        position: int,
        status: WorkflowStatus,
        *,
        tool_execution_id: UUID | None,
        result_json: str | None,
        error_code: str | None,
        duration_ms: int,
    ) -> bool:
        result = await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.owner_id == owner_id,
                WorkflowStep.position == position,
                WorkflowStep.status == WorkflowStatus.RUNNING,
            )
            .values(
                status=status,
                tool_execution_id=tool_execution_id,
                result_json=result_json,
                error_code=error_code,
                duration_ms=duration_ms,
                completed_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def advance_for_owner(
        self, owner_id: UUID, workflow_id: UUID, next_position: int
    ) -> bool:
        result = await self.session.execute(
            update(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status == WorkflowStatus.RUNNING,
                Workflow.cancel_requested.is_(False),
            )
            .values(
                current_step_position=next_position,
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def complete_for_owner(
        self, owner_id: UUID, workflow_id: UUID, result_json: str
    ) -> bool:
        incomplete_step = (
            select(WorkflowStep.id)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.owner_id == owner_id,
                WorkflowStep.status != WorkflowStatus.COMPLETED,
            )
            .exists()
        )
        result = await self.session.execute(
            update(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status == WorkflowStatus.RUNNING,
                Workflow.cancel_requested.is_(False),
                ~incomplete_step,
            )
            .values(
                status=WorkflowStatus.COMPLETED,
                current_step_position=Workflow.step_count,
                cancel_requested=False,
                result_json=result_json,
                error_code=None,
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def mark_cancel_requested_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> bool:
        result = await self.session.execute(
            update(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status == WorkflowStatus.RUNNING,
            )
            .values(cancel_requested=True, updated_at=func.now())
        )
        return result.rowcount == 1

    async def terminalize_for_owner(
        self,
        owner_id: UUID,
        workflow_id: UUID,
        status: WorkflowStatus,
        error_code: str,
    ) -> bool:
        if status not in {
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.TIMED_OUT,
        }:
            raise ValueError("workflow terminal status is invalid")
        result = await self.session.execute(
            update(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.owner_id == owner_id,
                Workflow.status.in_(
                    (WorkflowStatus.PENDING, WorkflowStatus.RUNNING)
                ),
            )
            .values(
                status=status,
                result_json=None,
                error_code=error_code,
                cancel_requested=(status == WorkflowStatus.CANCELLED),
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        if result.rowcount != 1:
            return False

        running_status = (
            WorkflowStatus.TIMED_OUT
            if status == WorkflowStatus.TIMED_OUT
            else (
                WorkflowStatus.CANCELLED
                if status == WorkflowStatus.CANCELLED
                else WorkflowStatus.FAILED
            )
        )
        running_error = {
            WorkflowStatus.TIMED_OUT: "step_timed_out",
            WorkflowStatus.CANCELLED: "workflow_cancelled",
            WorkflowStatus.FAILED: "internal_failure",
        }[status]
        await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.owner_id == owner_id,
                WorkflowStep.status == WorkflowStatus.RUNNING,
            )
            .values(
                status=running_status,
                result_json=None,
                error_code=running_error,
                duration_ms=0,
                completed_at=func.now(),
            )
        )
        await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.owner_id == owner_id,
                WorkflowStep.status == WorkflowStatus.PENDING,
            )
            .values(
                status=WorkflowStatus.CANCELLED,
                result_json=None,
                error_code="not_run",
                duration_ms=0,
                completed_at=func.now(),
            )
        )
        return True

    async def reconcile_interrupted(self) -> int:
        running_workflows = select(Workflow.id).where(
            Workflow.status == WorkflowStatus.RUNNING
        )
        await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id.in_(running_workflows),
                WorkflowStep.status == WorkflowStatus.RUNNING,
            )
            .values(
                status=WorkflowStatus.FAILED,
                result_json=None,
                error_code="server_restarted",
                duration_ms=0,
                completed_at=func.now(),
            )
        )
        await self.session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.workflow_id.in_(running_workflows),
                WorkflowStep.status == WorkflowStatus.PENDING,
            )
            .values(
                status=WorkflowStatus.CANCELLED,
                result_json=None,
                error_code="not_run",
                duration_ms=0,
                completed_at=func.now(),
            )
        )
        result = await self.session.execute(
            update(Workflow)
            .where(Workflow.status == WorkflowStatus.RUNNING)
            .values(
                status=WorkflowStatus.FAILED,
                result_json=None,
                error_code="server_restarted",
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount
