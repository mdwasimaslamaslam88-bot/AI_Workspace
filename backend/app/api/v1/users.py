from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse
from app.services.user import UserService


router = APIRouter(prefix="/users", tags=["Users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _request: Annotated[UserCreate, Body()] = UserCreate(),
) -> UserResponse:
    user = await UserService(session).create(User())
    return UserResponse.model_validate(user)
