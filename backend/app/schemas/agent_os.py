from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent_os.contracts import (
    AgentKind,
    AgentPermission,
    AgentRunStatus,
    VerificationFailure,
)
from app.ai.routing import ModelTask


class AgentRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=32_000)
    task: ModelTask
    specialist: AgentKind | None = None
    max_retries: int = Field(default=1, strict=True, ge=0, le=2)
    deadline_seconds: float = Field(default=180.0, gt=0, le=600.0)
    required_context_tokens: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    require_objective_evidence: bool = False

    @field_validator("goal")
    @classmethod
    def require_exact_nonblank_goal(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("agent goal must be exact nonblank text")
        return value


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AgentKind
    permissions: list[AgentPermission]
    registered: bool


class AgentOSCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[AgentProfileResponse] = Field(min_length=12, max_length=12)
    max_retries: int = 2
    max_deadline_seconds: int = 600
    active_runs: int = Field(ge=0, le=100)
    max_concurrency: int = 2
    persistence: str = "bounded_process_memory"


class AgentVerificationCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    passed: bool
    failure: VerificationFailure
    evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )


class AgentAttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    attempt: int = Field(strict=True, ge=1, le=3)
    agent: AgentKind
    model_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$",
    )
    verified: bool
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    checks: list[AgentVerificationCheckResponse] = Field(min_length=1, max_length=128)


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    task: ModelTask
    specialist: AgentKind | None
    status: AgentRunStatus
    created_at: datetime
    updated_at: datetime
    output: str | None = Field(default=None, max_length=262_144)
    failure_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )
    attempts: list[AgentAttemptResponse] = Field(default_factory=list, max_length=48)


class AgentRunPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentRunResponse] = Field(max_length=100)
