from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.user import (
    AccessTokenResponse,
    AccessTokenRotationRequest,
    UserCreate,
    UserProvisionResponse,
    UserResponse,
)
from app.services.user import UserService


router = APIRouter(prefix="/users", tags=["Users"])


@router.post(
    "",
    response_model=UserProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _request: Annotated[UserCreate, Body()] = UserCreate(),
) -> UserProvisionResponse:
    user, access_token = await UserService(session).provision_with_access_token()
    response.headers["Cache-Control"] = "no-store"
    return UserProvisionResponse(
        id=user.id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        access_token=access_token,
    )


@router.get("/me", response_model=UserResponse)
async def get_authenticated_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post(
    "/me/access-token/rotate",
    response_model=AccessTokenResponse,
)
async def rotate_access_token(
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _request: Annotated[AccessTokenRotationRequest, Body()] = (
        AccessTokenRotationRequest()
    ),
) -> AccessTokenResponse:
    expected_digest = current_user.access_token_digest
    if expected_digest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Access token rotation conflict",
        )

    access_token = await UserService(session).rotate_access_token(
        current_user.id,
        expected_digest,
    )
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Access token rotation conflict",
        )

    response.headers["Cache-Control"] = "no-store"
    return AccessTokenResponse(access_token=access_token)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await UserService(session).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(user)
