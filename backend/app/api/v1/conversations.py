from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.message import MessageRole
from app.models.user import User
from app.repositories.conversation import (
    ConversationCursor,
    ConversationPagination,
)
from app.repositories.message import (
    DEFAULT_MESSAGE_PAGE_SIZE,
    MAX_MESSAGE_PAGE_SIZE,
    MessageCursor,
    MessagePagination,
)
from app.schemas.conversation import (
    ConversationCreate,
    ConversationCreateResponse,
    ConversationCursorResponse,
    ConversationListQuery,
    ConversationPageResponse,
    ConversationRename,
    ConversationSummaryResponse,
)
from app.schemas.message import MessageCreate, MessagePageResponse, MessageResponse
from app.services.conversation import ConversationService
from app.services.message import MessageAppendConflictError, MessageService


router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.get("", response_model=ConversationPageResponse)
async def list_conversations(
    query: Annotated[ConversationListQuery, Query()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationPageResponse:
    page = await ConversationService(session).list_for_owner(
        current_user.id,
        ConversationPagination(
            limit=query.limit,
            cursor=(
                ConversationCursor(
                    updated_at=query.cursor_updated_at,
                    id=query.cursor_id,
                )
                if (
                    query.cursor_updated_at is not None
                    and query.cursor_id is not None
                )
                else None
            ),
        ),
    )
    return ConversationPageResponse(
        items=[
            ConversationSummaryResponse.model_validate(conversation)
            for conversation in page.items
        ],
        next_cursor=(
            ConversationCursorResponse(
                updated_at=page.next_cursor.updated_at,
                id=page.next_cursor.id,
            )
            if page.next_cursor is not None
            else None
        ),
    )


@router.post(
    "",
    response_model=ConversationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    request: ConversationCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationCreateResponse:
    created = await ConversationService(
        session
    ).create_with_initial_message_for_owner(
        current_user.id,
        request.title,
        MessageRole.USER,
        request.initial_message,
    )
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation could not be created",
        )

    conversation, initial_message = created
    await session.refresh(conversation, attribute_names=["updated_at"])
    return ConversationCreateResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        initial_message=MessageResponse.model_validate(initial_message),
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationSummaryResponse,
)
async def get_conversation(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummaryResponse:
    conversation = await ConversationService(session).get_for_owner(
        current_user.id,
        conversation_id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return ConversationSummaryResponse.model_validate(conversation)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummaryResponse,
)
async def rename_conversation(
    conversation_id: UUID,
    request: ConversationRename,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummaryResponse:
    conversation = await ConversationService(session).rename_for_owner(
        current_user.id,
        conversation_id,
        request.title,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return ConversationSummaryResponse.model_validate(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    deleted = await ConversationService(session).delete_for_owner(
        current_user.id,
        conversation_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    conversation_id: UUID,
    request: MessageCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    try:
        message = await MessageService(session).append_for_owner(
            current_user.id,
            conversation_id,
            MessageRole.USER,
            request.content,
        )
    except MessageAppendConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Message could not be appended",
        ) from None

    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return MessageResponse.model_validate(message)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePageResponse,
)
async def list_messages(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_MESSAGE_PAGE_SIZE),
    ] = DEFAULT_MESSAGE_PAGE_SIZE,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePageResponse:
    page = await MessageService(session).list_for_owner(
        current_user.id,
        conversation_id,
        MessagePagination(
            limit=limit,
            cursor=MessageCursor(cursor) if cursor is not None else None,
        ),
    )
    return MessagePageResponse(
        items=[MessageResponse.model_validate(message) for message in page.items],
        next_cursor=(
            page.next_cursor.sequence_number
            if page.next_cursor is not None
            else None
        ),
    )
