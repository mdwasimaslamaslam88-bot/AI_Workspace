from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommunicationCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["external_dependency"] = "external_dependency"
    configured: bool
    dependencies: list[str] = Field(min_length=1, max_length=4)


class CommunicationCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    phone_call: CommunicationCapability
    callback: CommunicationCapability
    video: CommunicationCapability
    screen_share: CommunicationCapability


class CommunicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    purpose: str = Field(min_length=1, max_length=240, pattern=r"\S")
    owner_approved: Literal[True]


class CommunicationAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    state: Literal["accepted_by_provider"]
