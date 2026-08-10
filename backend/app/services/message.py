from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole
from app.repositories.message import (
    MessagePage,
    MessagePagination,
    MessageRepository,
)


class MessageAppendConflictError(RuntimeError):
    """The database rejected an otherwise valid atomic message append."""


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
    ) -> Message | None:
        try:
            message = await self.repository.append_for_owner(
                owner_id,
                conversation_id,
                role,
                content,
            )
            if message is None:
                await self.session.rollback()
                return None

            await self.session.commit()
            return message
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
