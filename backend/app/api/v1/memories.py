from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.memory import (
    MemoryCreateRequest,
    MemoryPageResponse,
    MemoryResponse,
    MemorySearchQuery,
    MemorySearchResponse,
    MemorySearchResultResponse,
    MemorySettingResponse,
    MemorySettingUpdateRequest,
)
from app.services.memory import MemoryContentInvalidError, MemoryService


router = APIRouter(prefix="/memories", tags=["Memories"])


@router.get("", response_model=MemoryPageResponse)
async def list_memories(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_deleted: bool = True,
) -> MemoryPageResponse:
    items = await MemoryService(session).list_for_owner(
        current_user.id,
        include_deleted=include_deleted,
    )
    return MemoryPageResponse(
        items=[MemoryResponse.model_validate(item) for item in items]
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemoryResponse:
    try:
        memory = await MemoryService(session).create_for_owner(
            current_user.id,
            body.category,
            body.content,
        )
    except MemoryContentInvalidError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Memory content is invalid",
        ) from None
    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}", response_model=MemoryResponse)
async def forget_memory(
    memory_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemoryResponse:
    memory = await MemoryService(session).forget_for_owner(
        current_user.id,
        memory_id,
    )
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    return MemoryResponse.model_validate(memory)


@router.get("/settings", response_model=MemorySettingResponse)
async def get_memory_setting(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemorySettingResponse:
    setting = await MemoryService(session).setting_for_owner(current_user.id)
    return MemorySettingResponse.model_validate(setting, from_attributes=True)


@router.put("/settings", response_model=MemorySettingResponse)
async def update_memory_setting(
    body: MemorySettingUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemorySettingResponse:
    setting = await MemoryService(session).set_enabled_for_owner(
        current_user.id,
        body.enabled,
    )
    return MemorySettingResponse.model_validate(setting, from_attributes=True)


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    query: Annotated[MemorySearchQuery, Query()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemorySearchResponse:
    items = await MemoryService(session).retrieve_for_owner(
        current_user.id,
        query.query,
        limit=query.limit,
    )
    return MemorySearchResponse(
        items=[
            MemorySearchResultResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )
