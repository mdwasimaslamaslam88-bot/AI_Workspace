from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.agent_os.contracts import (
    AgentKind,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent_os.orchestrator import AgentOrchestrator
from app.ai.routing import ModelTask


MAX_AGENT_RUNS_PER_OWNER = 100


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    id: UUID
    task: ModelTask
    specialist: AgentKind | None
    status: AgentRunStatus
    created_at: datetime
    updated_at: datetime
    result: AgentRunResult | None = None


@dataclass(slots=True)
class _OwnedAgentRun:
    owner_id: UUID
    request: AgentRunRequest
    record: AgentRunRecord


class AgentRunNotFoundError(RuntimeError):
    """The run does not exist or belongs to another owner."""


class AgentRunManager:
    """Owner-isolated, bounded task lifecycle for interactive agent runs."""

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        *,
        max_runs_per_owner: int = MAX_AGENT_RUNS_PER_OWNER,
    ) -> None:
        if not 1 <= max_runs_per_owner <= MAX_AGENT_RUNS_PER_OWNER:
            raise ValueError("agent run retention bound is invalid")
        self.orchestrator = orchestrator
        self.max_runs_per_owner = max_runs_per_owner
        self._runs: dict[UUID, _OwnedAgentRun] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    async def submit(
        self,
        owner_id: UUID,
        request: AgentRunRequest,
    ) -> AgentRunRecord:
        if not isinstance(owner_id, UUID):
            raise TypeError("agent run owner must be a UUID")
        if not isinstance(request, AgentRunRequest):
            raise TypeError("agent run request is invalid")
        now = datetime.now(timezone.utc)
        record = AgentRunRecord(
            id=uuid4(),
            task=request.task,
            specialist=request.specialist,
            status=AgentRunStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._prune_terminal_for_owner(owner_id)
            if sum(
                run.owner_id == owner_id for run in self._runs.values()
            ) >= self.max_runs_per_owner:
                raise RuntimeError("agent run retention is full")
            self._runs[record.id] = _OwnedAgentRun(owner_id, request, record)
            task = asyncio.create_task(
                self._execute(record.id),
                name=f"agent-run-{record.id}",
            )
            self._tasks[record.id] = task
            task.add_done_callback(
                lambda completed, run_id=record.id: self._discard_task(
                    run_id, completed
                )
            )
        return record

    def _prune_terminal_for_owner(self, owner_id: UUID) -> None:
        owned = sorted(
            (
                run
                for run in self._runs.values()
                if run.owner_id == owner_id
                and run.record.status
                in {
                    AgentRunStatus.COMPLETED,
                    AgentRunStatus.FAILED,
                    AgentRunStatus.CANCELLED,
                    AgentRunStatus.TIMED_OUT,
                }
            ),
            key=lambda item: item.record.created_at,
        )
        owned_count = sum(
            run.owner_id == owner_id for run in self._runs.values()
        )
        for item in owned[: max(0, owned_count - self.max_runs_per_owner + 1)]:
            self._runs.pop(item.record.id, None)

    async def _execute(self, run_id: UUID) -> None:
        owned = self._runs.get(run_id)
        if owned is None:  # pragma: no cover - internal invariant
            return
        now = datetime.now(timezone.utc)
        owned.record = replace(
            owned.record,
            status=AgentRunStatus.RUNNING,
            updated_at=now,
        )
        try:
            result = await self.orchestrator.run(owned.request)
        except asyncio.CancelledError:
            result = AgentRunResult(
                status=AgentRunStatus.CANCELLED,
                plan=None,
                output=None,
                attempts=(),
                failure_code="agent_run_cancelled",
            )
        except Exception:
            result = AgentRunResult(
                status=AgentRunStatus.FAILED,
                plan=None,
                output=None,
                attempts=(),
                failure_code="agent_internal_failure",
            )
        owned.record = replace(
            owned.record,
            status=result.status,
            updated_at=datetime.now(timezone.utc),
            result=result,
        )

    def _discard_task(
        self,
        run_id: UUID,
        completed: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(run_id) is completed:
            self._tasks.pop(run_id, None)
        if not completed.cancelled():
            completed.exception()

    async def get_for_owner(
        self,
        owner_id: UUID,
        run_id: UUID,
    ) -> AgentRunRecord | None:
        async with self._lock:
            owned = self._runs.get(run_id)
            if owned is None or owned.owner_id != owner_id:
                return None
            return owned.record

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = 20,
    ) -> tuple[AgentRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("agent run list limit is invalid")
        async with self._lock:
            records = sorted(
                (
                    owned.record
                    for owned in self._runs.values()
                    if owned.owner_id == owner_id
                ),
                key=lambda record: (record.created_at, record.id),
                reverse=True,
            )
            return tuple(records[:limit])

    async def cancel_for_owner(
        self,
        owner_id: UUID,
        run_id: UUID,
    ) -> AgentRunRecord:
        async with self._lock:
            owned = self._runs.get(run_id)
            if owned is None or owned.owner_id != owner_id:
                raise AgentRunNotFoundError("agent run not found")
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        record = await self.get_for_owner(owner_id, run_id)
        if record is None:  # pragma: no cover - protected by owner record
            raise AgentRunNotFoundError("agent run not found")
        return record

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = tuple(task for task in self._tasks.values() if not task.done())
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
