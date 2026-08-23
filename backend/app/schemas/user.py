from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.user_session import MAX_USER_SESSION_LABEL_CHARACTERS


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccessTokenRotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        default=None,
        max_length=MAX_USER_SESSION_LABEL_CHARACTERS,
        pattern=r"\S",
    )


class UserSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        max_length=MAX_USER_SESSION_LABEL_CHARACTERS,
        pattern=r"\S",
    )


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserSessionResponse(BaseModel):
    id: UUID
    label: str | None
    created_at: datetime
    updated_at: datetime
    is_current: bool


class UserSessionPageResponse(BaseModel):
    items: list[UserSessionResponse]


class UserSessionProvisionResponse(AccessTokenResponse):
    session: UserSessionResponse


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class UserProvisionResponse(UserResponse):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
