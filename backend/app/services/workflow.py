from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, MutableMapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import ToolExecutionStatus
from app.models.workflow import (
    MAX_WORKFLOW_NAME_CHARACTERS,
    MAX_WORKFLOW_RESULT_JSON_CHARACTERS,
    MAX_WORKFLOW_STEPS,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from app.repositories.workflow import WorkflowRepository
from app.services.tool import (
    ToolInputInvalidError,
    ToolNotFoundError,
    ToolService,
    validate_tool_call,
)

MAX_WORKFLOW_WALL_SECONDS = 60.0
MAX_WORKFLOW_STEP_SECONDS = 10.0


class WorkflowNotFoundError(RuntimeError):
    """The workflow is not owned by the current user."""


class WorkflowNotStartableError(RuntimeError):
    """The workflow is not pending or is already scheduled."""


class WorkflowInputInvalidError(ValueError):
    """The workflow definition exceeds a fixed bound or tool schema."""


@dataclass(frozen=True, slots=True)
class WorkflowStepDraft:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _WorkflowStepExecutionSnapshot:
    position: int
    tool_name: str
    permission: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class WorkflowStepRecord:
    id: UUID
    position: int
    tool_name: str
    permission: str
    arguments: dict[str, Any]
    status: WorkflowStatus
    tool_execution_id: UUID | None
    result: Any | None
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class WorkflowRecord:
    id: UUID
    name: str | None
    status: WorkflowStatus
    step_count: int
    current_step_position: int | None
    cancel_requested: bool
    result: Any | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    steps: tuple[WorkflowStepRecord, ...]


def _canonical_json(value: Any, maximum: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowInputInvalidError("workflow data is invalid") from exc
    if len(encoded) > maximum:
        raise WorkflowInputInvalidError("workflow output exceeds its bound")
    return encoded


def _step_record(step: WorkflowStep) -> WorkflowStepRecord:
    return WorkflowStepRecord(
        id=step.id,
        position=step.position,
        tool_name=step.tool_name,
        permission=step.permission,
        arguments=json.loads(step.arguments_json),
        status=step.status,
        tool_execution_id=step.tool_execution_id,
        result=json.loads(step.result_json) if step.result_json is not None else None,
        error_code=step.error_code,
        started_at=step.started_at,
        completed_at=step.completed_at,
        duration_ms=step.duration_ms,
    )


def _workflow_record(workflow: Workflow) -> WorkflowRecord:
    return WorkflowRecord(
        id=workflow.id,
        name=workflow.name,
        status=workflow.status,
        step_count=workflow.step_count,
        current_step_position=workflow.current_step_position,
        cancel_requested=workflow.cancel_requested,
        result=(
            json.loads(workflow.result_json)
            if workflow.result_json is not None
            else None
        ),
        error_code=workflow.error_code,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        started_at=workflow.started_at,
        completed_at=workflow.completed_at,
        steps=tuple(_step_record(step) for step in workflow.steps),
    )


def _validated_name(name: str | None) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise TypeError("workflow name must be text")
    if not name.strip() or len(name) > MAX_WORKFLOW_NAME_CHARACTERS:
        raise WorkflowInputInvalidError("workflow name is invalid")
    return name


class WorkflowService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WorkflowRepository(session)

    async def create_for_owner(
        self,
        owner_id: UUID,
        name: str | None,
        steps: tuple[WorkflowStepDraft, ...],
    ) -> WorkflowRecord:
        name = _validated_name(name)
        if not 1 <= len(steps) <= MAX_WORKFLOW_STEPS:
            raise WorkflowInputInvalidError("workflow step count is invalid")
        persisted_steps: list[tuple[str, str, str]] = []
        try:
            for step in steps:
                if not isinstance(step, WorkflowStepDraft):
                    raise TypeError("workflow steps must be WorkflowStepDraft values")
                validated = validate_tool_call(step.tool_name, step.arguments)
                persisted_steps.append(
                    (
                        validated.definition.name,
                        validated.definition.permission,
                        validated.arguments_json,
                    )
                )
        except (ToolNotFoundError, ToolInputInvalidError) as exc:
            raise WorkflowInputInvalidError("workflow step is invalid") from exc

        try:
            workflow = await self.repository.create(
                owner_id, name, tuple(persisted_steps)
            )
            await self.session.commit()
            return _workflow_record(workflow)
        except BaseException:
            await self.session.rollback()
            raise

    async def get_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> WorkflowRecord | None:
        try:
            workflow = await self.repository.get_for_owner(owner_id, workflow_id)
            record = None if workflow is None else _workflow_record(workflow)
            await self.session.rollback()
            return record
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 20
    ) -> tuple[WorkflowRecord, ...]:
        try:
            workflows = await self.repository.list_for_owner(owner_id, limit=limit)
            records = tuple(_workflow_record(workflow) for workflow in workflows)
            await self.session.rollback()
            return records
        except BaseException:
            await self.session.rollback()
            raise


class WorkflowRunner:
    def __init__(
        self,
        session_factory,
        admission: asyncio.Semaphore,
        active_tasks: MutableMapping[UUID, asyncio.Task[None]],
        *,
        document_storage=None,
        document_admission: asyncio.Semaphore | None = None,
        wall_seconds: float = MAX_WORKFLOW_WALL_SECONDS,
        step_seconds: float = MAX_WORKFLOW_STEP_SECONDS,
    ) -> None:
        if session_factory is None:
            raise ValueError("workflow runner requires a database")
        if wall_seconds <= 0 or wall_seconds > MAX_WORKFLOW_WALL_SECONDS:
            raise ValueError("workflow wall deadline is outside its bound")
        if step_seconds <= 0 or step_seconds > MAX_WORKFLOW_STEP_SECONDS:
            raise ValueError("workflow step deadline is outside its bound")
        self.session_factory = session_factory
        self.admission = admission
        self.active_tasks = active_tasks
        self.document_storage = document_storage
        self.document_admission = document_admission
        self.wall_seconds = wall_seconds
        self.step_seconds = step_seconds

    async def start_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> WorkflowRecord:
        async with self.session_factory() as session:
            record = await WorkflowService(session).get_for_owner(
                owner_id, workflow_id
            )
        if record is None:
            raise WorkflowNotFoundError("workflow not found")
        if record.status is not WorkflowStatus.PENDING:
            raise WorkflowNotStartableError("workflow is not pending")
        existing = self.active_tasks.get(workflow_id)
        if existing is not None and not existing.done():
            raise WorkflowNotStartableError("workflow is already scheduled")
        task = asyncio.create_task(
            self._run(owner_id, workflow_id),
            name=f"workflow-{workflow_id}",
        )
        self.active_tasks[workflow_id] = task
        task.add_done_callback(
            lambda completed, identifier=workflow_id: self._discard_task(
                identifier, completed
            )
        )
        return record

    def _discard_task(
        self, workflow_id: UUID, completed: asyncio.Task[None]
    ) -> None:
        if self.active_tasks.get(workflow_id) is completed:
            self.active_tasks.pop(workflow_id, None)

    async def cancel_for_owner(
        self, owner_id: UUID, workflow_id: UUID
    ) -> WorkflowRecord:
        async with self.session_factory() as session:
            service = WorkflowService(session)
            workflow = await service.repository.get_for_owner(owner_id, workflow_id)
            if workflow is None:
                await session.rollback()
                raise WorkflowNotFoundError("workflow not found")
            if workflow.status == WorkflowStatus.PENDING:
                await service.repository.terminalize_for_owner(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.CANCELLED,
                    "workflow_cancelled",
                )
                await session.commit()
            elif workflow.status == WorkflowStatus.RUNNING:
                await service.repository.mark_cancel_requested_for_owner(
                    owner_id, workflow_id
                )
                await session.commit()
            else:
                await session.rollback()

        task = self.active_tasks.get(workflow_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self.session_factory() as session:
            record = await WorkflowService(session).get_for_owner(
                owner_id, workflow_id
            )
        if record is None:  # pragma: no cover - protected by owner row
            raise WorkflowNotFoundError("workflow not found")
        return record

    async def shutdown(self) -> None:
        tasks = tuple(task for task in self.active_tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, owner_id: UUID, workflow_id: UUID) -> None:
        try:
            async with asyncio.timeout(self.wall_seconds):
                async with self.admission:
                    await self._run_claimed(owner_id, workflow_id)
        except TimeoutError:
            await asyncio.shield(
                self._terminalize(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.TIMED_OUT,
                    "workflow_timed_out",
                )
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._terminalize(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.CANCELLED,
                    "workflow_cancelled",
                )
            )
            raise
        except BaseException:
            await asyncio.shield(
                self._terminalize(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.FAILED,
                    "internal_failure",
                )
            )

    async def _run_claimed(self, owner_id: UUID, workflow_id: UUID) -> None:
        async with self.session_factory() as session:
            repository = WorkflowRepository(session)
            if not await repository.claim_for_owner(owner_id, workflow_id):
                await session.rollback()
                return
            await session.commit()
            workflow = await repository.get_for_owner(owner_id, workflow_id)
            if workflow is None:
                await session.rollback()
                return
            steps = tuple(
                _WorkflowStepExecutionSnapshot(
                    position=step.position,
                    tool_name=step.tool_name,
                    permission=step.permission,
                    arguments_json=step.arguments_json,
                )
                for step in workflow.steps
            )
            await session.rollback()

            for step in steps:
                current = await repository.get_for_owner(owner_id, workflow_id)
                if (
                    current is None
                    or current.status is not WorkflowStatus.RUNNING
                    or current.cancel_requested
                ):
                    await session.rollback()
                    await self._terminalize(
                        owner_id,
                        workflow_id,
                        WorkflowStatus.CANCELLED,
                        "workflow_cancelled",
                    )
                    return
                await session.rollback()
                if not await repository.claim_step_for_owner(
                    owner_id, workflow_id, step.position
                ):
                    await session.rollback()
                    return
                await session.commit()

                validated = validate_tool_call(
                    step.tool_name, json.loads(step.arguments_json)
                )
                if validated.definition.permission != step.permission:
                    await self._terminalize(
                        owner_id,
                        workflow_id,
                        WorkflowStatus.FAILED,
                        "internal_failure",
                    )
                    return
                try:
                    execution = await asyncio.wait_for(
                        ToolService(
                            session,
                            document_storage=self.document_storage,
                            document_admission=self.document_admission,
                        ).execute_for_owner(
                            owner_id,
                            step.tool_name,
                            validated.arguments,
                            initiator="workflow",
                        ),
                        timeout=self.step_seconds,
                    )
                except asyncio.TimeoutError:
                    await self._terminalize(
                        owner_id,
                        workflow_id,
                        WorkflowStatus.TIMED_OUT,
                        "workflow_timed_out",
                    )
                    return

                step_status = {
                    ToolExecutionStatus.COMPLETED: WorkflowStatus.COMPLETED,
                    ToolExecutionStatus.FAILED: WorkflowStatus.FAILED,
                    ToolExecutionStatus.TIMED_OUT: WorkflowStatus.TIMED_OUT,
                    ToolExecutionStatus.CANCELLED: WorkflowStatus.CANCELLED,
                    ToolExecutionStatus.RUNNING: WorkflowStatus.FAILED,
                }[execution.status]
                result_json = (
                    _canonical_json(execution.result, 16_384)
                    if execution.status is ToolExecutionStatus.COMPLETED
                    else None
                )
                error_code = (
                    None
                    if execution.status is ToolExecutionStatus.COMPLETED
                    else execution.error_code or "internal_failure"
                )
                if not await repository.finish_step_for_owner(
                    owner_id,
                    workflow_id,
                    step.position,
                    step_status,
                    tool_execution_id=execution.id,
                    result_json=result_json,
                    error_code=error_code,
                    duration_ms=execution.duration_ms or 0,
                ):
                    await session.rollback()
                    return
                await session.commit()

                if step_status is not WorkflowStatus.COMPLETED:
                    terminal_status = (
                        WorkflowStatus.TIMED_OUT
                        if step_status is WorkflowStatus.TIMED_OUT
                        else WorkflowStatus.FAILED
                    )
                    await self._terminalize(
                        owner_id,
                        workflow_id,
                        terminal_status,
                        (
                            "workflow_timed_out"
                            if terminal_status is WorkflowStatus.TIMED_OUT
                            else "step_failed"
                        ),
                    )
                    return

                if step.position < len(steps):
                    if not await repository.advance_for_owner(
                        owner_id, workflow_id, step.position + 1
                    ):
                        await session.rollback()
                        await self._terminalize(
                            owner_id,
                            workflow_id,
                            WorkflowStatus.CANCELLED,
                            "workflow_cancelled",
                        )
                        return
                    await session.commit()

            completed = await repository.get_for_owner(owner_id, workflow_id)
            if completed is None:
                await session.rollback()
                return
            result = {
                "steps": [
                    {
                        "position": item.position,
                        "tool_name": item.tool_name,
                        "result": (
                            json.loads(item.result_json)
                            if item.result_json is not None
                            else None
                        ),
                    }
                    for item in completed.steps
                ]
            }
            try:
                result_json = _canonical_json(
                    result, MAX_WORKFLOW_RESULT_JSON_CHARACTERS
                )
            except WorkflowInputInvalidError:
                await session.rollback()
                await self._terminalize(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.FAILED,
                    "output_too_large",
                )
                return
            if not await repository.complete_for_owner(
                owner_id, workflow_id, result_json
            ):
                await session.rollback()
                await self._terminalize(
                    owner_id,
                    workflow_id,
                    WorkflowStatus.CANCELLED,
                    "workflow_cancelled",
                )
                return
            await session.commit()

    async def _terminalize(
        self,
        owner_id: UUID,
        workflow_id: UUID,
        status: WorkflowStatus,
        error_code: str,
    ) -> None:
        async with self.session_factory() as session:
            repository = WorkflowRepository(session)
            try:
                changed = await repository.terminalize_for_owner(
                    owner_id, workflow_id, status, error_code
                )
                if changed:
                    await session.commit()
                else:
                    await session.rollback()
            except BaseException:
                await session.rollback()
                raise


async def reconcile_workflows(session_factory) -> int:
    if session_factory is None:
        return 0
    async with session_factory() as session:
        repository = WorkflowRepository(session)
        try:
            count = await repository.reconcile_interrupted()
            await session.commit()
            return count
        except BaseException:
            await session.rollback()
            raise
