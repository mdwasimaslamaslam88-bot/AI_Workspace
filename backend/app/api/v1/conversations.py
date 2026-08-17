from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import ModelRuntimeUnavailableError
from app.ai.generation import (
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)
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
from app.schemas.ai import (
    ConversationTextGenerationRequest,
    ConversationTextGenerationResponse,
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
from app.services.conversation_generation import (
    ConversationChangedDuringGenerationError,
    ConversationGenerationContextTooLargeError,
    ConversationGenerationModelNotFoundError,
    ConversationGenerationModelUnavailableError,
    ConversationGenerationNotFoundError,
    ConversationGenerationNotReadyError,
    ConversationGenerationService,
)


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
        system_prompt=request.system_prompt,
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


@router.post(
    "/{conversation_id}/messages/generate",
    response_model=ConversationTextGenerationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_assistant_message(
    conversation_id: UUID,
    generation_request: ConversationTextGenerationRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationTextGenerationResponse:
    catalog = getattr(request.app.state, "model_catalog", None)
    generation_router = getattr(
        request.app.state,
        "text_generation_router",
        None,
    )
    if catalog is None or generation_router is None:
        raise RuntimeError("Local text generation is not configured")

    try:
        message = await ConversationGenerationService(
            session,
            catalog,
            generation_router,
        ).generate_for_owner(
            current_user.id,
            conversation_id,
            generation_request.model_id,
            user_message=generation_request.user_message,
            max_output_tokens=generation_request.max_output_tokens,
            temperature=generation_request.temperature,
            seed=generation_request.seed,
            top_p=generation_request.top_p,
            top_k=generation_request.top_k,
            min_p=generation_request.min_p,
            repeat_penalty=generation_request.repeat_penalty,
            repeat_last_n=generation_request.repeat_last_n,
            typical_p=generation_request.typical_p,
            presence_penalty=generation_request.presence_penalty,
        )
    except ConversationGenerationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        ) from None
    except ConversationGenerationModelNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        ) from None
    except ConversationGenerationNotReadyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is not ready for generation",
        ) from None
    except ConversationChangedDuringGenerationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation changed during generation",
        ) from None
    except TextGenerationRuntimeUnsupportedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Model does not support text generation",
        ) from None
    except MessageAppendConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Message could not be appended",
        ) from None
    except ConversationGenerationContextTooLargeError:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Conversation context is too large",
        ) from None
    except (
        ConversationGenerationModelUnavailableError,
        ModelRuntimeUnavailableError,
        TextGenerationRuntimeUnavailableError,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local model runtime unavailable",
        ) from None

    return ConversationTextGenerationResponse(
        model_id=generation_request.model_id,
        message=MessageResponse.model_validate(message),
    )


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
