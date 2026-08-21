from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole, validate_message_content
from app.repositories.message import (
    GenerationContextSnapshot,
    MessageAttachmentClaimError,
    MessagePage,
    MessagePagination,
    MessageRepository,
)


class MessageAppendConflictError(RuntimeError):
    """The database rejected an otherwise valid atomic message append."""


class MessageAttachmentUnavailableError(RuntimeError):
    """One or more requested assets cannot be claimed by the message."""


class MessageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MessageRepository(session)

    async def append_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
        *,
        expected_sequence_number: int | None = None,
        attachment_ids: tuple[UUID, ...] = (),
    ) -> Message | None:
        validate_message_content(content)
        try:
            if expected_sequence_number is None:
                message = await self.repository.append_for_owner(
                    owner_id,
                    conversation_id,
                    role,
                    content,
                    **({"attachment_ids": attachment_ids} if attachment_ids else {}),
                )
            else:
                message = await self.repository.append_for_owner(
                    owner_id,
                    conversation_id,
                    role,
                    content,
                    expected_sequence_number=expected_sequence_number,
                    **({"attachment_ids": attachment_ids} if attachment_ids else {}),
                )
            if message is None:
                await self.session.rollback()
                return None

            await self.session.commit()
            return message
        except MessageAttachmentClaimError as exc:
            await self.session.rollback()
            raise MessageAttachmentUnavailableError(
                "one or more attachments are unavailable"
            ) from exc

        except IntegrityError as exc:
            await self.session.rollback()
            raise MessageAppendConflictError(
                "message append violated a persistence constraint"
            ) from exc
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        pagination: MessagePagination | None = None,
    ) -> MessagePage:
        try:
            return await self.repository.list_for_owner(
                owner_id,
                conversation_id,
                pagination,
            )
        except BaseException:
            await self.session.rollback()
            raise

    async def list_generation_context_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        max_messages: int,
        max_context_characters: int,
    ) -> GenerationContextSnapshot:
        try:
            return await self.repository.list_generation_context_for_owner(
                owner_id,
                conversation_id,
                max_messages=max_messages,
                max_context_characters=max_context_characters,
            )
        except BaseException:
            await self.session.rollback()
            raise
