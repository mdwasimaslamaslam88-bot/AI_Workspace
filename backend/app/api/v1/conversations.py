from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.message import MessageRole
from app.models.user import User
from app.schemas.conversation import ConversationCreate, ConversationCreateResponse
from app.schemas.message import MessageResponse
from app.services.conversation import ConversationService


router = APIRouter(prefix="/conversations", tags=["Conversations"])


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
