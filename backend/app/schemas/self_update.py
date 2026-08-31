from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.maintenance import UpdateStatus


class UpdateGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    passed: bool


class SelfUpdateStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    status: UpdateStatus
    version: str | None = Field(default=None, max_length=64)
    candidate_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    checkpoint_ready: bool
    rollback_ready: bool
    activation_requires_owner: bool
    gates: list[UpdateGateResponse] = Field(default_factory=list, max_length=32)
    failure_code: str | None = Field(default=None, max_length=96)


class SelfUpdateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["update", "cancel"]
