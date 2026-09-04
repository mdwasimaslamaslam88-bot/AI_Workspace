from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import UUID

from sqlalchemy import func, select

from app.agent_os.contracts import (
    AgentAttempt,
    AgentInputSource,
    AgentKind,
    AgentPermission,
    AgentPlan,
    AgentPlanStep,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    VerificationCheck,
    VerificationFailure,
    VerificationReport,
)
from app.agent_os.runtime import (
    AgentRunEventRecord,
    AgentRunRecord,
    EXECUTING_AGENT_STATUSES,
    StoredAgentRun,
)
from app.ai.routing import ModelTask
from app.models.agent_mission import AgentMission, AgentMissionEvent


def _compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _request_payload(value: AgentRunRequest) -> dict[str, object]:
    return {
        "goal": value.goal,
        "task": value.task.value,
        "source": value.source.value,
        "specialist": value.specialist.value if value.specialist else None,
        "permissions": sorted(permission.value for permission in value.permissions),
        "max_retries": value.max_retries,
        "deadline_seconds": value.deadline_seconds,
        "required_context_tokens": value.required_context_tokens,
        "require_objective_evidence": value.require_objective_evidence,
        "allow_external_models": value.allow_external_models,
        "require_owner_approval": value.require_owner_approval,
    }


def _request_from(payload: dict[str, object]) -> AgentRunRequest:
    specialist = payload["specialist"]
    return AgentRunRequest(
        goal=str(payload["goal"]),
        task=ModelTask(str(payload["task"])),
        source=AgentInputSource(str(payload["source"])),
        specialist=None if specialist is None else AgentKind(str(specialist)),
        permissions=frozenset(
            AgentPermission(str(permission))
            for permission in payload["permissions"]  # type: ignore[union-attr]
        ),
        max_retries=int(payload["max_retries"]),
        deadline_seconds=float(payload["deadline_seconds"]),
        required_context_tokens=int(payload["required_context_tokens"]),
        require_objective_evidence=bool(payload["require_objective_evidence"]),
        allow_external_models=bool(payload["allow_external_models"]),
        require_owner_approval=bool(payload["require_owner_approval"]),
    )


def _plan_payload(value: AgentPlan | None) -> object:
    if value is None:
        return None
    return [
        {
            "step_id": step.step_id,
            "agent": step.agent.value,
            "task": step.task.value,
            "instruction": step.instruction,
            "permissions": sorted(permission.value for permission in step.permissions),
            "requires_objective_evidence": step.requires_objective_evidence,
        }
        for step in value.steps
    ]


def _plan_from(value: object) -> AgentPlan | None:
    if value is None:
        return None
    assert isinstance(value, list)
    return AgentPlan(
        steps=tuple(
            AgentPlanStep(
                step_id=str(raw["step_id"]),
                agent=AgentKind(str(raw["agent"])),
                task=ModelTask(str(raw["task"])),
                instruction=str(raw["instruction"]),
                permissions=frozenset(
                    AgentPermission(str(permission))
                    for permission in raw["permissions"]
                ),
                requires_objective_evidence=bool(
                    raw["requires_objective_evidence"]
                ),
            )
            for raw in value
        )
    )


def _result_payload(value: AgentRunResult | None) -> object:
    if value is None:
        return None
    return {
        "status": value.status.value,
        "plan": _plan_payload(value.plan),
        "output": value.output,
        "failure_code": value.failure_code,
        "attempts": [
            {
                "step_id": attempt.step_id,
                "attempt": attempt.attempt,
                "agent": attempt.agent.value,
                "model_id": attempt.model_id,
                "verification": {
                    "passed": attempt.verification.passed,
                    "output_sha256": attempt.verification.output_sha256,
                    "checks": [
                        {
                            "check_id": check.check_id,
                            "passed": check.passed,
                            "failure": check.failure.value,
                            "evidence_sha256": check.evidence_sha256,
                        }
                        for check in attempt.verification.checks
                    ],
                },
            }
            for attempt in value.attempts
        ],
    }


def _result_from(value: object) -> AgentRunResult | None:
    if value is None:
        return None
    assert isinstance(value, dict)
    attempts = []
    for raw in value["attempts"]:
        verification = raw["verification"]
        attempts.append(
            AgentAttempt(
                step_id=str(raw["step_id"]),
                attempt=int(raw["attempt"]),
                agent=AgentKind(str(raw["agent"])),
                model_id=(
                    None if raw["model_id"] is None else str(raw["model_id"])
                ),
                verification=VerificationReport(
                    passed=bool(verification["passed"]),
                    output_sha256=str(verification["output_sha256"]),
                    checks=tuple(
                        VerificationCheck(
                            check_id=str(check["check_id"]),
                            passed=bool(check["passed"]),
                            failure=VerificationFailure(str(check["failure"])),
                            evidence_sha256=(
                                None
                                if check["evidence_sha256"] is None
                                else str(check["evidence_sha256"])
                            ),
                        )
                        for check in verification["checks"]
                    ),
                ),
            )
        )
    return AgentRunResult(
        status=AgentRunStatus(str(value["status"])),
        plan=_plan_from(value["plan"]),
        output=None if value["output"] is None else str(value["output"]),
        attempts=tuple(attempts),
        failure_code=(
            None if value["failure_code"] is None else str(value["failure_code"])
        ),
    )


def _record_payload(value: AgentRunRecord) -> dict[str, object]:
    return {
        "id": str(value.id),
        "goal": value.goal,
        "source": value.source.value,
        "task": value.task.value,
        "specialist": value.specialist.value if value.specialist else None,
        "status": value.status.value,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "plan": _plan_payload(value.plan),
        "events": [
            {
                "sequence": event.sequence,
                "status": event.status.value,
                "created_at": event.created_at.isoformat(),
                "step_id": event.step_id,
                "attempt": event.attempt,
                "agent": event.agent.value if event.agent else None,
                "model_id": event.model_id,
                "action": event.action,
                "detail_sha256": event.detail_sha256,
            }
            for event in value.events
        ],
        "result": _result_payload(value.result),
        "pause_requested": value.pause_requested,
        "requires_approval": value.requires_approval,
        "approved": value.approved,
        "revision": value.revision,
        "manual_retry_count": value.manual_retry_count,
    }


def _record_from(payload: dict[str, object]) -> AgentRunRecord:
    specialist = payload["specialist"]
    events = payload["events"]
    assert isinstance(events, list)
    return AgentRunRecord(
        id=UUID(str(payload["id"])),
        goal=str(payload["goal"]),
        source=AgentInputSource(str(payload["source"])),
        task=ModelTask(str(payload["task"])),
        specialist=None if specialist is None else AgentKind(str(specialist)),
        status=AgentRunStatus(str(payload["status"])),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        plan=_plan_from(payload["plan"]),
        events=tuple(
            AgentRunEventRecord(
                sequence=int(raw["sequence"]),
                status=AgentRunStatus(str(raw["status"])),
                created_at=datetime.fromisoformat(str(raw["created_at"])),
                step_id=None if raw["step_id"] is None else str(raw["step_id"]),
                attempt=None if raw["attempt"] is None else int(raw["attempt"]),
                agent=None if raw["agent"] is None else AgentKind(str(raw["agent"])),
                model_id=(
                    None if raw["model_id"] is None else str(raw["model_id"])
                ),
                action=str(raw.get("action", "status")),
                detail_sha256=(
                    None
                    if raw.get("detail_sha256") is None
                    else str(raw["detail_sha256"])
                ),
            )
            for raw in events
        ),
        result=_result_from(payload["result"]),
        pause_requested=bool(payload["pause_requested"]),
        requires_approval=bool(payload["requires_approval"]),
        approved=bool(payload["approved"]),
        revision=int(payload["revision"]),
        manual_retry_count=int(payload["manual_retry_count"]),
    )


class DatabaseAgentRunStore:
    """PostgreSQL-backed mission snapshots with append-only content-free audit."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _stored(row: AgentMission) -> StoredAgentRun:
        request = _request_from(json.loads(row.request_json))
        record = _record_from(json.loads(row.record_json))
        return StoredAgentRun(row.owner_id, request, record)

    async def save(self, run: StoredAgentRun) -> None:
        request_json = _compact(_request_payload(run.request))
        record_json = _compact(_record_payload(run.record))
        if len(request_json) > 65_536 or len(record_json) > 1_048_576:
            raise ValueError("agent mission persistence payload is too large")
        async with self.session_factory() as session:
            result = await session.execute(
                select(AgentMission)
                .where(
                    AgentMission.id == run.record.id,
                    AgentMission.owner_id == run.owner_id,
                )
                .with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = AgentMission(id=run.record.id, owner_id=run.owner_id)
                session.add(row)
                existing_sequence = 0
            else:
                existing_sequence = int(
                    await session.scalar(
                        select(func.coalesce(func.max(AgentMissionEvent.sequence), 0))
                        .where(
                            AgentMissionEvent.mission_id == run.record.id,
                            AgentMissionEvent.owner_id == run.owner_id,
                        )
                    )
                    or 0
                )
            row.status = run.record.status.value
            row.request_json = request_json
            row.record_json = record_json
            row.pause_requested = run.record.pause_requested
            row.requires_approval = run.record.requires_approval
            row.approved = run.record.approved
            row.revision = run.record.revision
            row.manual_retry_count = run.record.manual_retry_count
            row.created_at = run.record.created_at
            row.updated_at = run.record.updated_at
            for event in run.record.events:
                if event.sequence <= existing_sequence:
                    continue
                session.add(
                    AgentMissionEvent(
                        mission_id=run.record.id,
                        owner_id=run.owner_id,
                        sequence=event.sequence,
                        action=event.action,
                        status=event.status.value,
                        step_id=event.step_id,
                        attempt=event.attempt,
                        agent=event.agent.value if event.agent else None,
                        model_id=event.model_id,
                        detail_sha256=event.detail_sha256,
                        created_at=event.created_at,
                    )
                )
            await session.commit()

    async def get_for_owner(
        self, owner_id: UUID, run_id: UUID
    ) -> StoredAgentRun | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(AgentMission).where(
                    AgentMission.id == run_id, AgentMission.owner_id == owner_id
                )
            )
            row = result.scalar_one_or_none()
            return None if row is None else self._stored(row)

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int
    ) -> tuple[StoredAgentRun, ...]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(AgentMission)
                .where(AgentMission.owner_id == owner_id)
                .order_by(AgentMission.created_at.desc(), AgentMission.id.desc())
                .limit(limit)
            )
            return tuple(self._stored(row) for row in result.scalars())

    async def initialize(self) -> tuple[StoredAgentRun, ...]:
        nonterminal = tuple(
            status.value
            for status in AgentRunStatus
            if status not in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.TIMED_OUT,
            }
        )
        async with self.session_factory() as session:
            result = await session.execute(
                select(AgentMission)
                .where(AgentMission.status.in_(nonterminal))
                .order_by(AgentMission.created_at, AgentMission.id)
                .limit(1001)
            )
            rows = list(result.scalars())
            if len(rows) > 1000:
                raise RuntimeError("active persistent mission recovery limit exceeded")
            recovered: list[StoredAgentRun] = []
            for row in rows:
                stored = self._stored(row)
                if stored.record.status in EXECUTING_AGENT_STATUSES - {
                    AgentRunStatus.QUEUED
                }:
                    now = datetime.now(timezone.utc)
                    event = AgentRunEventRecord(
                        sequence=(
                            stored.record.events[-1].sequence + 1
                            if stored.record.events
                            else 1
                        ),
                        status=AgentRunStatus.PAUSED,
                        created_at=now,
                        action="recovered_paused",
                    )
                    stored = StoredAgentRun(
                        stored.owner_id,
                        stored.request,
                        replace(
                            stored.record,
                            status=AgentRunStatus.PAUSED,
                            pause_requested=True,
                            updated_at=now,
                            result=None,
                            events=(*stored.record.events, event)[-128:],
                        ),
                    )
                    row.status = AgentRunStatus.PAUSED.value
                    row.pause_requested = True
                    row.record_json = _compact(_record_payload(stored.record))
                    row.updated_at = now
                    session.add(
                        AgentMissionEvent(
                            mission_id=stored.record.id,
                            owner_id=stored.owner_id,
                            sequence=event.sequence,
                            action=event.action,
                            status=event.status.value,
                            created_at=event.created_at,
                        )
                    )
                recovered.append(stored)
            await session.commit()
            return tuple(recovered)
