from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories.conversation import (
    ConversationPage,
    ConversationPagination,
    ConversationRepository,
)


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ConversationRepository(session)

    async def create(self, owner_id: UUID, title: str | None) -> Conversation:
        try:
            conversation = await self.repository.create(owner_id, title)
            await self.session.commit()
            return conversation
        except BaseException:
            await self.session.rollback()
            raise

    async def get_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        try:
            return await self.repository.get_for_owner(owner_id, conversation_id)
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self,
        owner_id: UUID,
        pagination: ConversationPagination | None = None,
    ) -> ConversationPage:
        try:
            return await self.repository.list_for_owner(owner_id, pagination)
        except BaseException:
            await self.session.rollback()
            raise

    async def rename_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        title: str | None,
    ) -> Conversation | None:
        try:
            conversation = await self.repository.rename_for_owner(
                owner_id,
                conversation_id,
                title,
            )
            if conversation is None:
                await self.session.rollback()
                return None

            await self.session.commit()
            return conversation
        except BaseException:
            await self.session.rollback()
            raise

    async def delete_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> bool:
        try:
            deleted = await self.repository.delete_for_owner(
                owner_id,
                conversation_id,
            )
            if not deleted:
                await self.session.rollback()
                return False

            await self.session.commit()
            return True
        except BaseException:
            await self.session.rollback()
            raise
