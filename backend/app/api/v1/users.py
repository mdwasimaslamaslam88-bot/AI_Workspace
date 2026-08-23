from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.security import is_user_provisioning_authorized
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.user import (
    AccessTokenResponse,
    AccessTokenRotationRequest,
    UserCreate,
    UserProvisionResponse,
    UserResponse,
    UserSessionCreate,
    UserSessionPageResponse,
    UserSessionProvisionResponse,
    UserSessionResponse,
    UserSessionUpdate,
)
from app.services.user import UserService, UserSessionLimitError


router = APIRouter(prefix="/users", tags=["Users"])


def _authenticated_session_identity(
    current_user: User,
    *,
    conflict_detail: str = "Access session conflict",
) -> tuple[UUID, str]:
    session_id = current_user.authenticated_session_id
    access_token_digest = current_user.authenticated_session_digest
    if session_id is None or access_token_digest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_detail,
        )
    return session_id, access_token_digest


def _require_user_provisioning_authorization(
    provisioning_token: Annotated[
        str | None,
        Header(alias="X-User-Provisioning-Token"),
    ] = None,
) -> None:
    if not is_user_provisioning_authorized(
        provisioning_token,
        settings.USER_PROVISIONING_TOKEN_DIGEST,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User provisioning is not authorized",
        )


@router.post(
    "",
    response_model=UserProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    response: Response,
    _authorized: Annotated[
        None,
        Depends(_require_user_provisioning_authorization),
    ],
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
    session_id, expected_digest = _authenticated_session_identity(
        current_user,
        conflict_detail="Access token rotation conflict",
    )

    access_token = await UserService(session).rotate_access_token(
        current_user.id,
        session_id,
        expected_digest,
    )
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Access token rotation conflict",
        )

    response.headers["Cache-Control"] = "no-store"
    return AccessTokenResponse(access_token=access_token)


@router.get(
    "/me/sessions",
    response_model=UserSessionPageResponse,
)
async def list_access_sessions(
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserSessionPageResponse:
    current_session_id, _digest = _authenticated_session_identity(current_user)
    access_sessions = await UserService(session).list_active_sessions_for_owner(
        current_user.id
    )
    response.headers["Cache-Control"] = "private, no-store"
    return UserSessionPageResponse(
        items=[
            UserSessionResponse(
                id=access_session.id,
                label=access_session.label,
                created_at=access_session.created_at,
                updated_at=access_session.updated_at,
                is_current=access_session.id == current_session_id,
            )
            for access_session in access_sessions
        ]
    )


@router.post(
    "/me/sessions",
    response_model=UserSessionProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_access_session(
    request: UserSessionCreate,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserSessionProvisionResponse:
    try:
        created = await UserService(session).create_access_session_for_owner(
            current_user.id,
            request.label,
        )
    except UserSessionLimitError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active session limit reached",
        ) from None
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    access_session, access_token = created
    response.headers["Cache-Control"] = "no-store"
    return UserSessionProvisionResponse(
        access_token=access_token,
        session=UserSessionResponse(
            id=access_session.id,
            label=access_session.label,
            created_at=access_session.created_at,
            updated_at=access_session.updated_at,
            is_current=False,
        ),
    )


@router.patch(
    "/me/sessions/current",
    response_model=UserSessionResponse,
)
async def rename_current_access_session(
    request: UserSessionUpdate,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserSessionResponse:
    current_session_id, _digest = _authenticated_session_identity(current_user)
    access_session = await UserService(session).rename_active_session_for_owner(
        current_user.id,
        current_session_id,
        request.label,
    )
    if access_session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Access session conflict",
        )
    response.headers["Cache-Control"] = "private, no-store"
    return UserSessionResponse(
        id=access_session.id,
        label=access_session.label,
        created_at=access_session.created_at,
        updated_at=access_session.updated_at,
        is_current=True,
    )


@router.delete(
    "/me/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_current_access_session(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    current_session_id, _digest = _authenticated_session_identity(current_user)
    revoked = await UserService(session).revoke_active_session_for_owner(
        current_user.id,
        current_session_id,
    )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Access session conflict",
        )


@router.delete(
    "/me/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_access_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    revoked = await UserService(session).revoke_active_session_for_owner(
        current_user.id,
        session_id,
    )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access session not found",
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(current_user)
