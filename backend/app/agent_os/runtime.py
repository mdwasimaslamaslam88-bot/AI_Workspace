from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from typing import Protocol
from uuid import UUID, uuid4

from app.agent_os.contracts import (
    AgentInputSource,
    AgentKind,
    AgentLifecycleUpdate,
    AgentPlan,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent_os.orchestrator import AgentOrchestrator
from app.ai.routing import ModelTask


MAX_AGENT_RUNS_PER_OWNER = 100
MAX_AGENT_EVENTS_PER_RUN = 128
MAX_AGENT_MANUAL_RETRIES = 3
MAX_AGENT_REVISIONS = 16
TERMINAL_AGENT_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
    }
)
EXECUTING_AGENT_STATUSES = frozenset(
    {
        AgentRunStatus.QUEUED,
        AgentRunStatus.PLANNING,
        AgentRunStatus.RUNNING,
        AgentRunStatus.VERIFYING,
        AgentRunStatus.RETRYING,
    }
)


@dataclass(frozen=True, slots=True)
class AgentRunEventRecord:
    sequence: int
    status: AgentRunStatus
    created_at: datetime
    step_id: str | None = None
    attempt: int | None = None
    agent: AgentKind | None = None
    model_id: str | None = None
    action: str = "status"
    detail_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    id: UUID
    goal: str
    source: AgentInputSource
    task: ModelTask
    specialist: AgentKind | None
    status: AgentRunStatus
    created_at: datetime
    updated_at: datetime
    plan: AgentPlan | None = None
    events: tuple[AgentRunEventRecord, ...] = ()
    result: AgentRunResult | None = None
    pause_requested: bool = False
    requires_approval: bool = False
    approved: bool = False
    revision: int = 1
    manual_retry_count: int = 0


@dataclass(slots=True)
class _OwnedAgentRun:
    owner_id: UUID
    request: AgentRunRequest
    record: AgentRunRecord


@dataclass(frozen=True, slots=True)
class StoredAgentRun:
    owner_id: UUID
    request: AgentRunRequest
    record: AgentRunRecord


class AgentRunStore(Protocol):
    async def save(self, run: StoredAgentRun) -> None: ...

    async def get_for_owner(
        self, owner_id: UUID, run_id: UUID
    ) -> StoredAgentRun | None: ...

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int
    ) -> tuple[StoredAgentRun, ...]: ...

    async def initialize(self) -> tuple[StoredAgentRun, ...]: ...


class AgentRunNotFoundError(RuntimeError):
    """The run does not exist or belongs to another owner."""


class AgentRunConflictError(RuntimeError):
    """The owner action is not valid for the current mission state."""


class AgentRunManager:
    """Owner-isolated, persistent-capable, bounded Agent OS scheduler."""

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        *,
        max_runs_per_owner: int = MAX_AGENT_RUNS_PER_OWNER,
        store: AgentRunStore | None = None,
    ) -> None:
        if not 1 <= max_runs_per_owner <= MAX_AGENT_RUNS_PER_OWNER:
            raise ValueError("agent run retention bound is invalid")
        self.orchestrator = orchestrator
        self.max_runs_per_owner = max_runs_per_owner
        self.store = store
        self._runs: dict[UUID, _OwnedAgentRun] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._persist_locks: dict[UUID, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    @property
    def persistence(self) -> str:
        return (
            "postgresql_checkpoint_scheduler"
            if self.store is not None
            else "bounded_process_memory"
        )

    async def initialize(self) -> None:
        if self.store is None:
            return
        recovered = await self.store.initialize()
        async with self._lock:
            for stored in recovered:
                self._runs[stored.record.id] = _OwnedAgentRun(
                    stored.owner_id, stored.request, stored.record
                )
            for stored in recovered:
                if stored.record.status is AgentRunStatus.QUEUED:
                    self._schedule_locked(stored.record.id)

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
        initial_status = (
            AgentRunStatus.NEEDS_APPROVAL
            if request.require_owner_approval
            else AgentRunStatus.QUEUED
        )
        record = AgentRunRecord(
            id=uuid4(),
            goal=request.goal,
            source=request.source,
            task=request.task,
            specialist=request.specialist,
            status=initial_status,
            created_at=now,
            updated_at=now,
            events=(
                AgentRunEventRecord(
                    sequence=1,
                    status=initial_status,
                    created_at=now,
                    action=(
                        "approval_required"
                        if request.require_owner_approval
                        else "submitted"
                    ),
                ),
            ),
            requires_approval=request.require_owner_approval,
        )
        async with self._lock:
            self._prune_terminal_for_owner(owner_id)
            if sum(
                run.owner_id == owner_id for run in self._runs.values()
            ) >= self.max_runs_per_owner:
                raise RuntimeError("agent run retention is full")
            self._runs[record.id] = _OwnedAgentRun(owner_id, request, record)
        await self._persist(record.id)
        if initial_status is AgentRunStatus.QUEUED:
            async with self._lock:
                self._schedule_locked(record.id)
        return record

    def _schedule_locked(self, run_id: UUID) -> None:
        current = self._tasks.get(run_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._execute(run_id), name=f"agent-run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(
            lambda completed, scheduled_id=run_id: self._discard_task(
                scheduled_id, completed
            )
        )

    def _prune_terminal_for_owner(self, owner_id: UUID) -> None:
        owned = sorted(
            (
                run
                for run in self._runs.values()
                if run.owner_id == owner_id
                and run.record.status in TERMINAL_AGENT_STATUSES
            ),
            key=lambda item: item.record.created_at,
        )
        owned_count = sum(
            run.owner_id == owner_id for run in self._runs.values()
        )
        for item in owned[: max(0, owned_count - self.max_runs_per_owner + 1)]:
            self._runs.pop(item.record.id, None)
            self._persist_locks.pop(item.record.id, None)

    async def _execute(self, run_id: UUID) -> None:
        owned = self._runs.get(run_id)
        if owned is None:  # pragma: no cover - internal invariant
            return

        async def lifecycle(update: AgentLifecycleUpdate) -> None:
            async with self._lock:
                current = self._runs.get(run_id)
                if current is None:  # pragma: no cover - internal invariant
                    return
                self._append_lifecycle_event(current, update)
            await self._persist(run_id)

        paused = False
        try:
            result = await self.orchestrator.run(owned.request, lifecycle=lifecycle)
        except asyncio.CancelledError:
            current = self._runs.get(run_id)
            paused = bool(current is not None and current.record.pause_requested)
            result = None if paused else AgentRunResult(
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
        async with self._lock:
            current = self._runs.get(run_id)
            if current is None:  # pragma: no cover - internal invariant
                return
            if paused:
                current.record = replace(current.record, result=None)
                self._append_control_event(
                    current, action="paused", status=AgentRunStatus.PAUSED
                )
            else:
                assert result is not None
                now = datetime.now(timezone.utc)
                event = AgentRunEventRecord(
                    sequence=self._next_event_sequence(current.record),
                    status=result.status,
                    created_at=now,
                    action=(
                        "cancelled"
                        if result.status is AgentRunStatus.CANCELLED
                        else "status"
                    ),
                )
                current.record = replace(
                    current.record,
                    status=result.status,
                    updated_at=now,
                    plan=result.plan or current.record.plan,
                    events=self._bounded_events(current.record.events, event),
                    result=result,
                    pause_requested=False,
                )
        await self._persist(run_id)

    @staticmethod
    def _next_event_sequence(record: AgentRunRecord) -> int:
        return record.events[-1].sequence + 1 if record.events else 1

    @staticmethod
    def _bounded_events(
        events: tuple[AgentRunEventRecord, ...],
        event: AgentRunEventRecord,
    ) -> tuple[AgentRunEventRecord, ...]:
        return (*events, event)[-MAX_AGENT_EVENTS_PER_RUN:]

    def _append_lifecycle_event(
        self,
        owned: _OwnedAgentRun,
        update: AgentLifecycleUpdate,
    ) -> None:
        now = datetime.now(timezone.utc)
        event = AgentRunEventRecord(
            sequence=self._next_event_sequence(owned.record),
            status=update.status,
            created_at=now,
            step_id=update.step_id,
            attempt=update.attempt,
            agent=update.agent,
            model_id=update.model_id,
        )
        owned.record = replace(
            owned.record,
            status=update.status,
            updated_at=now,
            plan=update.plan or owned.record.plan,
            events=self._bounded_events(owned.record.events, event),
        )

    def _append_control_event(
        self,
        owned: _OwnedAgentRun,
        *,
        action: str,
        status: AgentRunStatus | None = None,
        detail: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        resolved_status = status or owned.record.status
        event = AgentRunEventRecord(
            sequence=self._next_event_sequence(owned.record),
            status=resolved_status,
            created_at=now,
            action=action,
            detail_sha256=(
                hashlib.sha256(detail.encode("utf-8")).hexdigest()
                if detail is not None
                else None
            ),
        )
        owned.record = replace(
            owned.record,
            status=resolved_status,
            updated_at=now,
            events=self._bounded_events(owned.record.events, event),
        )

    async def _persist(self, run_id: UUID) -> None:
        if self.store is None:
            return
        async with self._lock:
            persist_lock = self._persist_locks.setdefault(run_id, asyncio.Lock())
        async with persist_lock:
            async with self._lock:
                owned = self._runs.get(run_id)
                snapshot = (
                    None
                    if owned is None
                    else StoredAgentRun(owned.owner_id, owned.request, owned.record)
                )
            if snapshot is not None:
                await self.store.save(snapshot)

    async def _load_owned(
        self, owner_id: UUID, run_id: UUID
    ) -> _OwnedAgentRun | None:
        async with self._lock:
            owned = self._runs.get(run_id)
            if owned is not None:
                return owned if owned.owner_id == owner_id else None
        if self.store is None:
            return None
        stored = await self.store.get_for_owner(owner_id, run_id)
        if stored is None:
            return None
        loaded = _OwnedAgentRun(stored.owner_id, stored.request, stored.record)
        async with self._lock:
            existing = self._runs.setdefault(run_id, loaded)
            return existing if existing.owner_id == owner_id else None

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
        owned = await self._load_owned(owner_id, run_id)
        return None if owned is None else owned.record

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = 20,
    ) -> tuple[AgentRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("agent run list limit is invalid")
        if self.store is not None:
            stored = await self.store.list_for_owner(owner_id, limit=limit)
            async with self._lock:
                for item in stored:
                    existing = self._runs.get(item.record.id)
                    if existing is None:
                        self._runs[item.record.id] = _OwnedAgentRun(
                            item.owner_id, item.request, item.record
                        )
            return tuple(
                self._runs[item.record.id].record
                for item in stored
                if self._runs[item.record.id].owner_id == owner_id
            )
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
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status in TERMINAL_AGENT_STATUSES:
                return owned.record
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                owned.record = replace(owned.record, pause_requested=False)
                task.cancel()
            else:
                result = AgentRunResult(
                    status=AgentRunStatus.CANCELLED,
                    plan=owned.record.plan,
                    output=None,
                    attempts=(),
                    failure_code="agent_run_cancelled",
                )
                owned.record = replace(
                    owned.record,
                    pause_requested=False,
                    result=result,
                )
                self._append_control_event(
                    owned,
                    action="cancelled",
                    status=AgentRunStatus.CANCELLED,
                )
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status not in TERMINAL_AGENT_STATUSES:
                result = AgentRunResult(
                    status=AgentRunStatus.CANCELLED,
                    plan=owned.record.plan,
                    output=None,
                    attempts=(),
                    failure_code="agent_run_cancelled",
                )
                owned.record = replace(
                    owned.record,
                    pause_requested=False,
                    result=result,
                )
                self._append_control_event(
                    owned,
                    action="cancelled",
                    status=AgentRunStatus.CANCELLED,
                )
        await self._persist(run_id)
        record = await self.get_for_owner(owner_id, run_id)
        if record is None:  # pragma: no cover - protected by owner record
            raise AgentRunNotFoundError("agent run not found")
        return record

    async def pause_for_owner(self, owner_id: UUID, run_id: UUID) -> AgentRunRecord:
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status not in EXECUTING_AGENT_STATUSES:
                raise AgentRunConflictError("agent run cannot be paused")
            owned.record = replace(owned.record, pause_requested=True)
            self._append_control_event(owned, action="pause_requested")
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
            else:
                self._append_control_event(
                    owned, action="paused", status=AgentRunStatus.PAUSED
                )
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            owned = self._runs[run_id]
            if (
                owned.record.status in EXECUTING_AGENT_STATUSES
                and owned.record.pause_requested
            ):
                owned.record = replace(owned.record, result=None)
                self._append_control_event(
                    owned,
                    action="paused",
                    status=AgentRunStatus.PAUSED,
                )
        await self._persist(run_id)
        record = await self.get_for_owner(owner_id, run_id)
        if record is None:
            raise AgentRunNotFoundError("agent run not found")
        return record

    async def resume_for_owner(self, owner_id: UUID, run_id: UUID) -> AgentRunRecord:
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status is not AgentRunStatus.PAUSED:
                raise AgentRunConflictError("agent run is not paused")
            owned.record = replace(
                owned.record, pause_requested=False, plan=None, result=None
            )
            self._append_control_event(
                owned, action="resumed", status=AgentRunStatus.QUEUED
            )
        await self._persist(run_id)
        async with self._lock:
            self._schedule_locked(run_id)
            return self._runs[run_id].record

    async def approve_for_owner(self, owner_id: UUID, run_id: UUID) -> AgentRunRecord:
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status is not AgentRunStatus.NEEDS_APPROVAL:
                raise AgentRunConflictError("agent run is not awaiting approval")
            owned.record = replace(owned.record, approved=True)
            self._append_control_event(
                owned, action="approved", status=AgentRunStatus.QUEUED
            )
        await self._persist(run_id)
        async with self._lock:
            self._schedule_locked(run_id)
            return self._runs[run_id].record

    async def modify_for_owner(
        self, owner_id: UUID, run_id: UUID, goal: str
    ) -> AgentRunRecord:
        if (
            not isinstance(goal, str)
            or not goal.strip()
            or goal != goal.strip()
            or len(goal) > 32_000
        ):
            raise ValueError("agent goal is invalid")
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        if loaded.record.status in EXECUTING_AGENT_STATUSES:
            await self.pause_for_owner(owner_id, run_id)
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status not in {
                AgentRunStatus.PAUSED,
                AgentRunStatus.NEEDS_APPROVAL,
            }:
                raise AgentRunConflictError("agent run cannot be modified")
            if owned.record.revision >= MAX_AGENT_REVISIONS:
                raise AgentRunConflictError("agent run revision limit reached")
            owned.request = replace(owned.request, goal=goal)
            next_status = (
                AgentRunStatus.NEEDS_APPROVAL
                if owned.record.requires_approval
                else AgentRunStatus.QUEUED
            )
            owned.record = replace(
                owned.record,
                goal=goal,
                pause_requested=False,
                approved=(
                    False
                    if owned.record.requires_approval
                    else owned.record.approved
                ),
                revision=owned.record.revision + 1,
                plan=None,
                result=None,
            )
            self._append_control_event(
                owned, action="modified", status=next_status, detail=goal
            )
        await self._persist(run_id)
        if next_status is AgentRunStatus.QUEUED:
            async with self._lock:
                self._schedule_locked(run_id)
        record = await self.get_for_owner(owner_id, run_id)
        if record is None:
            raise AgentRunNotFoundError("agent run not found")
        return record

    async def retry_for_owner(self, owner_id: UUID, run_id: UUID) -> AgentRunRecord:
        loaded = await self._load_owned(owner_id, run_id)
        if loaded is None:
            raise AgentRunNotFoundError("agent run not found")
        async with self._lock:
            owned = self._runs[run_id]
            if owned.record.status not in {
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.TIMED_OUT,
            }:
                raise AgentRunConflictError("agent run is not manually retryable")
            if owned.record.manual_retry_count >= MAX_AGENT_MANUAL_RETRIES:
                raise AgentRunConflictError("manual retry limit reached")
            count = owned.record.manual_retry_count + 1
            owned.record = replace(
                owned.record,
                pause_requested=False,
                manual_retry_count=count,
                plan=None,
                result=None,
            )
            self._append_control_event(
                owned,
                action="manual_retry",
                status=AgentRunStatus.QUEUED,
                detail=str(count),
            )
        await self._persist(run_id)
        async with self._lock:
            self._schedule_locked(run_id)
            return self._runs[run_id].record

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = tuple(task for task in self._tasks.values() if not task.done())
            checkpointed: list[UUID] = []
            for run_id, task in tuple(self._tasks.items()):
                if (
                    not task.done()
                    and run_id in self._runs
                    and self._runs[run_id].record.status in EXECUTING_AGENT_STATUSES
                ):
                    owned = self._runs[run_id]
                    owned.record = replace(owned.record, pause_requested=True)
                    self._append_control_event(owned, action="shutdown_checkpoint")
                    checkpointed.append(run_id)
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            for run_id in checkpointed:
                owned = self._runs.get(run_id)
                if (
                    owned is not None
                    and owned.record.status in EXECUTING_AGENT_STATUSES
                    and owned.record.pause_requested
                ):
                    owned.record = replace(owned.record, result=None)
                    self._append_control_event(
                        owned,
                        action="paused",
                        status=AgentRunStatus.PAUSED,
                    )
        for run_id in checkpointed:
            await self._persist(run_id)
